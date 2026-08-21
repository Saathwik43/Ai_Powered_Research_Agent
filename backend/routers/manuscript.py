"""Drafting the paper: section generation, drafts, LaTeX export, and the
venue / gap / guideline advisors that operate on a draft."""

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ai.gap_analysis import analyze_gaps
from ai.guideline_alignment import align_guidelines
from ai.latex_export import VENUES, export_manuscript
from ai.llm_provider import current_model, current_provider
from ai.model_allowlist import assert_allowed_model
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


def _public_draft(doc: dict) -> dict:
    """Drop internals and expose Mongo `_id` as a string `id`."""
    out = {k: v for k, v in doc.items() if k not in ("_id", "user_id")}
    oid = doc.get("_id")
    if oid is not None:
        out["id"] = str(oid)
    return out


async def _find_user_manuscript(user_id: str, topic: Optional[str] = None, draft_id: Optional[str] = None):
    """Locate one of the caller's drafts by id, then exact topic, then stripped topic."""
    collection = db["manuscripts"]
    if draft_id:
        try:
            oid = ObjectId(draft_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid draft id.")
        return await collection.find_one({"user_id": user_id, "_id": oid})

    if topic is None:
        return None

    doc = await collection.find_one({"user_id": user_id, "topic": topic})
    if doc:
        return doc
    stripped = topic.strip()
    if stripped != topic:
        return await collection.find_one({"user_id": user_id, "topic": stripped})
    return None


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

    provider, model = assert_allowed_model(
        payload.provider,
        payload.model if hasattr(payload, "model") else None,
    )
    current_provider.set(provider)
    current_model.set(model)

    gen = generate_section_stream(
        payload.topic,
        payload.section,
        payload.context,
        payload.citation_style,
        payload.mode,
        provider,
        model,
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
        payload.target_text,
        payload.target_start,
        payload.target_end,
        payload.target_kind,
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
    raw_topic = payload.topic or ""
    topic = raw_topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required.")
    now = datetime.now(timezone.utc).isoformat()
    existing = await collection.find_one({"user_id": user_id, "topic": topic})
    if not existing and raw_topic != topic:
        existing = await collection.find_one({"user_id": user_id, "topic": raw_topic})
    if existing:
        await collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "topic": topic,
                "content": payload.content or {},
                "gap_analysis": payload.gap_analysis,
                "manuscript_refs": payload.manuscript_refs,
                "citation_style": payload.citation_style,
                "updated_at": now
            }}
        )
        return {"message": "Draft updated.", "topic": topic, "id": str(existing["_id"])}
    else:
        result = await collection.insert_one({
            "user_id": user_id,
            "topic": topic,
            "content": payload.content or {},
            "gap_analysis": payload.gap_analysis,
            "manuscript_refs": payload.manuscript_refs,
            "citation_style": payload.citation_style,
            "created_at": now,
            "updated_at": now,
        })
        return {"message": "Draft saved.", "topic": topic, "id": str(result.inserted_id)}


@router.get("/api/manuscript/load")
@limiter.limit("30/minute")
async def load_manuscript_draft(
    request: Request,
    current_user: dict = Depends(get_current_user),
    topic: Optional[str] = None,
    draft_id: Optional[str] = None,
):
    if not draft_id and topic is None:
        raise HTTPException(status_code=400, detail="Topic or draft id is required.")
    user_id = current_user["user_id"]
    doc = await _find_user_manuscript(user_id, topic=topic, draft_id=draft_id)
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")
    return {"data": _public_draft(doc)}


def _flatten_references(manuscript_refs) -> list:
    """
    Legacy fallback only -- see _export_references below.

    manuscript_refs is an opaque Dict[str, Any] whose shape the frontend owns;
    defensively flatten whatever paper dicts are nested inside. In practice the
    client saves {index: formatted_citation_string}, so the values are strings
    and this returns [] -- which is exactly why the export shipped an empty
    references.bib on every run (audit L4).
    """
    out = []
    if not manuscript_refs:
        return out
    if isinstance(manuscript_refs, list):
        return [r for r in manuscript_refs if isinstance(r, dict)]
    if isinstance(manuscript_refs, dict):
        for k, v in manuscript_refs.items():
            if isinstance(v, list):
                out.extend(r for r in v if isinstance(r, dict))
            elif isinstance(v, dict):
                # Carry the key across as the citation index when the nested
                # dict does not already name one.
                out.append({"index": str(k), **v} if "index" not in v else v)
    return out


async def _export_references(user_id: str, topic: str, doc: dict) -> list:
    """
    The reference set for an export, with its `[N]` numbering intact.

    Reads `manuscript_references`, which `_store_reference_snapshot` writes at
    generation time with an explicit `index` on every entry -- the same numbering
    the writer LLM was given. That makes the `[N]` -> `\\cite{}` mapping a lookup
    rather than an inference, which matters: C1 and C4 were both citation
    numbering drifting out of sync with the papers behind it.

    Falls back to the display-only `manuscript_refs` for drafts written before
    snapshots were persisted.
    """
    try:
        snapshot_doc = await db["manuscript_references"].find_one(
            {"user_id": user_id, "topic": topic}, {"_id": 0, "references": 1}
        )
    except Exception as e:
        logger.warning(f"Reference snapshot lookup failed for '{topic}': {e}")
        snapshot_doc = None

    references = (snapshot_doc or {}).get("references") or []
    if references:
        return references

    legacy = _flatten_references(doc.get("manuscript_refs"))
    if legacy:
        logger.info(f"Export for '{topic}' fell back to manuscript_refs ({len(legacy)} refs)")
    return legacy


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
    doc = await _find_user_manuscript(user_id, topic=topic)
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")

    content = doc.get("content") or {}
    if not content:
        raise HTTPException(status_code=400, detail="Draft has no section content to export.")
    references = await _export_references(user_id, topic, doc)

    try:
        tex, bib, warnings = export_manuscript(
            topic=topic,
            content=content,
            venue=venue.lower(),
            references=references,
            author_name=payload.author_name,
            author_affil=payload.author_affil,
        )
    except ValueError as e:
        # Includes a citation/reference mismatch. Deliberately a hard 400: a
        # paper that compiles cleanly while citing the wrong sources is worse
        # than one that refuses to export.
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
    readme += (
        f"\nReferences: {len(references)} entries in references.bib, cited with \\cite{{}}.\n"
        "A citation map is included as comments at the top of paper.tex -- worth a\n"
        "30-second read to confirm [1] is the paper you expect.\n"
    )
    if warnings:
        readme += "\nWarnings:\n" + "".join(f"  - {w}\n" for w in warnings)

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
        {"user_id": 0, "content": 0}
    ).sort("updated_at", -1)
    drafts = [_public_draft(doc) async for doc in cursor]
    return {"data": drafts}


@router.delete("/api/manuscript/delete")
@limiter.limit("20/minute")
async def delete_manuscript_draft(
    request: Request,
    current_user: dict = Depends(get_current_user),
    topic: Optional[str] = None,
    draft_id: Optional[str] = None,
):
    if not draft_id and topic is None:
        raise HTTPException(status_code=400, detail="Topic or draft id is required.")

    user_id = current_user["user_id"]
    doc = await _find_user_manuscript(user_id, topic=topic, draft_id=draft_id)
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")

    await db["manuscripts"].delete_one({"_id": doc["_id"], "user_id": user_id})

    stored_topic = doc.get("topic")
    try:
        await db["manuscript_references"].delete_one({"user_id": user_id, "topic": stored_topic})
        if isinstance(stored_topic, str) and stored_topic.strip() != stored_topic:
            await db["manuscript_references"].delete_one({"user_id": user_id, "topic": stored_topic.strip()})
    except Exception as e:
        logger.warning(f"Failed to delete reference snapshot for '{stored_topic}': {e}")

    return {"message": "Draft deleted.", "topic": stored_topic, "id": str(doc["_id"])}


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
