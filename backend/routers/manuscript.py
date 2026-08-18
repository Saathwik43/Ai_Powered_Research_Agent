"""Drafting the paper: section generation, drafts, LaTeX export, and the
venue / gap / guideline advisors that operate on a draft."""

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ai.gap_analysis import analyze_gaps
from ai.guideline_alignment import align_guidelines
from ai.latex_export import VENUES, export_manuscript
from ai.llm_provider import current_model, current_provider
from ai.manuscript_generation import edit_section
from ai.venue_recommendation import recommend_venues
from core.auth import get_current_user
from core.limiter import limiter
from core.database import db
from schemas import (
    GapAnalysisPayload,
    GuidelinePayload,
    LatexExportPayload,
    ManuscriptEditPayload,
    ManuscriptPayload,
    ManuscriptSavePayload,
    ManuscriptStreamPayload,
    VenuePayload,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["manuscript"])


# ─── Manuscript Generation ─────────────────────────────────────────────────────

@router.post("/api/manuscript")
@limiter.limit("5/minute")
async def draft_manuscript(request: Request, payload: ManuscriptPayload, current_user: dict = Depends(get_current_user)):
    from ai.manuscript_generation import generate_section
    content, flags = await generate_section(payload.topic, payload.section, payload.context, payload.citation_style)
    if '{"error": "topic_unclear"}' in content:
        raise HTTPException(status_code=400, detail="The provided topic is unclear or appears to be nonsense.")

    response = {"section": payload.section, "content": content}
    response.update(flags)
    return response


async def _sse_wrap(generator):
    async for chunk in generator:
        yield f"data: {json.dumps(chunk)}\n\n"

@router.post("/api/manuscript/stream")
@limiter.limit("15/minute")
async def draft_manuscript_stream(request: Request, payload: ManuscriptStreamPayload, current_user: dict = Depends(get_current_user)):
    from ai.manuscript_generation import generate_section_stream
    # No usage_tracker.check_quota here per user request, rely on provider limits

    current_provider.set(payload.provider)
    current_model.set(payload.model if hasattr(payload, 'model') else None)

    gen = generate_section_stream(
        payload.topic,
        payload.section,
        payload.context,
        payload.citation_style,
        payload.mode,
        payload.provider,
        payload.model
    )

    return StreamingResponse(_sse_wrap(gen), media_type="text/event-stream")

@router.post("/api/manuscript/edit")
@limiter.limit("5/minute")
async def edit_manuscript_section(request: Request, payload: ManuscriptEditPayload, current_user: dict = Depends(get_current_user)):
    content, flags = await edit_section(
        payload.topic,
        payload.section,
        payload.current_content,
        payload.instructions,
        payload.citation_style,
    )
    # Flags travel with the revision so the diff view can warn *before* the user
    # accepts it, rather than leaving the previous generation's verdict on screen.
    response = {"section": payload.section, "content": content}
    response.update(flags)
    return response

@router.post("/api/manuscript/save")
@limiter.limit("20/minute")
async def save_manuscript_draft(request: Request, payload: ManuscriptSavePayload, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    now = datetime.now(timezone.utc).isoformat()
    existing = await collection.find_one({"user_id": user_id, "topic": payload.topic})
    if existing:
        await collection.update_one(
            {"user_id": user_id, "topic": payload.topic},
            {"$set": {
                "content": payload.content,
                "gap_analysis": payload.gap_analysis,
                "manuscript_refs": payload.manuscript_refs,
                "citation_style": payload.citation_style,
                "updated_at": now
            }}
        )
        return {"message": "Draft updated.", "topic": payload.topic}
    else:
        await collection.insert_one({
            "user_id": user_id,
            "topic": payload.topic,
            "content": payload.content,
            "gap_analysis": payload.gap_analysis,
            "manuscript_refs": payload.manuscript_refs,
            "citation_style": payload.citation_style,
            "created_at": now,
            "updated_at": now,
        })
        return {"message": "Draft saved.", "topic": payload.topic}


@router.get("/api/manuscript/load")
@limiter.limit("30/minute")
async def load_manuscript_draft(request: Request, topic: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    doc = await collection.find_one({"user_id": user_id, "topic": topic}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")
    return {"data": doc}


def _flatten_references(manuscript_refs) -> list:
    """manuscript_refs is stored as an opaque Dict[str, Any] (frontend-defined
    shape) -- defensively flatten whatever paper dicts are nested inside."""
    out = []
    if not manuscript_refs:
        return out
    if isinstance(manuscript_refs, list):
        return [r for r in manuscript_refs if isinstance(r, dict)]
    if isinstance(manuscript_refs, dict):
        for v in manuscript_refs.values():
            if isinstance(v, list):
                out.extend(r for r in v if isinstance(r, dict))
            elif isinstance(v, dict):
                out.append(v)
    return out


@router.post("/api/manuscript/export-latex")
@limiter.limit("10/minute")
async def export_manuscript_latex(
    request: Request,
    payload: LatexExportPayload,
    current_user: dict = Depends(get_current_user),
):
    topic = payload.topic
    venue = payload.venue
    if venue.lower() not in VENUES:
        raise HTTPException(status_code=400, detail=f"Unknown venue. Supported: {list(VENUES.keys())}")

    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    doc = await collection.find_one({"user_id": user_id, "topic": topic}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")

    content = doc.get("content") or {}
    if not content:
        raise HTTPException(status_code=400, detail="Draft has no section content to export.")
    references = _flatten_references(doc.get("manuscript_refs"))

    try:
        tex, bib = export_manuscript(
            topic=topic,
            content=content,
            venue=venue.lower(),
            references=references,
            author_name=payload.author_name,
            author_affil=payload.author_affil,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    readme = (
        f"LaTeX export for: {topic}\n"
        f"Venue: {venue.upper()}\n\n"
        "This zip contains paper.tex + references.bib only. You still need:\n"
        f"  1. The official {venue.upper()} class file (not included -- get the current\n"
        "     version from the publisher's author center or Overleaf's official template\n"
        "     gallery, since redistributing a bundled copy here would go stale).\n"
        "  2. Any Mermaid diagram in the draft is left as a '% TODO' comment in paper.tex\n"
        "     with the original Mermaid source preserved below it -- recreate it as a\n"
        "     real LaTeX figure before submission.\n"
        "  3. Venue-specific extras not auto-filled: ACM CCS Concepts, Elsevier Highlights\n"
        "     (if applicable) -- placeholders are marked TODO; replace manually.\n"
        "  4. Compile in Overleaf (upload this zip + the class file) or a local TeX toolchain.\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("paper.tex", tex)
        zf.writestr("references.bib", bib)
        zf.writestr("README.txt", readme)
    buf.seek(0)

    safe_topic = re.sub(r"[^a-zA-Z0-9]+", "_", topic).strip("_")[:50] or "manuscript"
    filename = f"{safe_topic}_{venue.lower()}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/manuscript/list")
@limiter.limit("30/minute")
async def list_manuscript_drafts(request: Request, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    cursor = collection.find(
        {"user_id": user_id},
        {"_id": 0, "user_id": 0, "content": 0}
    ).sort("updated_at", -1)
    drafts = [doc async for doc in cursor]
    return {"data": drafts}


# ─── Venue Recommendations ─────────────────────────────────────────────────────

@router.post("/api/venues")
async def get_venues(payload: VenuePayload, current_user: dict = Depends(get_current_user)):
    result = await recommend_venues(payload.abstract, payload.domain)
    return result

@router.post("/api/gap-analysis")
@limiter.limit("5/minute")
async def gap_analysis_endpoint(request: Request, payload: GapAnalysisPayload, current_user: dict = Depends(get_current_user)):
    try:
        result = await analyze_gaps(payload.topic)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in gap analysis: {e}")
        raise HTTPException(status_code=500, detail="Gap analysis failed.")


# ─── Guideline Alignment ───────────────────────────────────────────────────────

@router.post("/api/guidelines")
async def get_guidelines(payload: GuidelinePayload, current_user: dict = Depends(get_current_user)):
    if not payload.venue.get("name"):
        raise HTTPException(status_code=400, detail="Venue name is required.")
    result = await align_guidelines(payload.manuscript, payload.venue)
    return {"data": result}
