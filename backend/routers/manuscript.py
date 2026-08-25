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
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ai.gap_analysis import analyze_gaps
from ai.guideline_alignment import align_guidelines
from ai.latex_export import VENUES, export_manuscript, match_manuscript_refs_to_papers
from ai.llm_provider import current_model, current_provider
from ai.model_allowlist import bound_requested_model
from ai.manuscript_generation import edit_section
from ai.venue_recommendation import recommend_venues
from core.auth import get_current_user
from core.limiter import limiter
from core.database import db
from services import usage_tracker
from schemas import (
    GapAnalysisPayload,
    GuidelinePayload,
    LatexExportPayload,
    ManuscriptEditPayload,
    ManuscriptPayload,
    ManuscriptRestorePayload,
    ManuscriptSavePayload,
    ManuscriptStreamPayload,
    ResearchPayload,
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


async def _sse_wrap(generator, user_id: str | None = None, request: Request | None = None):
    """Serialise chunks as SSE frames, re-asserting the caller's identity first.

    The response body is iterated after the endpoint returns, so anything that
    reads `usage_tracker.current_user_id` downstream — quota checks, token
    billing, `_store_reference_snapshot` — depends on the contextvar reaching
    this generator. `SecurityHeadersMiddleware` is pure ASGI so it does now, but
    setting it here keeps that true if a `BaseHTTPMiddleware` is ever added back.
    """
    if user_id:
        usage_tracker.current_user_id.set(user_id)
    try:
        async for chunk in generator:
            if request is not None and await request.is_disconnected():
                break
            yield f"data: {json.dumps(chunk)}\n\n"
    finally:
        aclose = getattr(generator, "aclose", None)
        if aclose is not None:
            await aclose()

@router.post("/api/manuscript/stream")
@limiter.limit("15/minute")
async def draft_manuscript_stream(request: Request, payload: ManuscriptStreamPayload, current_user: dict = Depends(get_current_user)):
    from ai.manuscript_generation import generate_section_stream
    # No usage_tracker.check_quota here per user request, rely on provider limits

    provider, model = bound_requested_model(payload.mode, payload.provider, payload.model)
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

    return StreamingResponse(
        _sse_wrap(gen, current_user["user_id"], request), media_type="text/event-stream"
    )

# ─── Background research (1.5) ─────────────────────────────────────────────────

@router.post("/api/manuscript/research/prepare")
@limiter.limit("20/minute")
async def prepare_research(request: Request, payload: ResearchPayload, current_user: dict = Depends(get_current_user)):
    """Start (or join) the research run for a topic, and return its state.

    Called as soon as a topic is known — a draft being opened, a topic being
    typed — so the pipeline is already finished by the time Generate is pressed.
    """
    from services import research_jobs

    topic = (payload.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required.")
    return {"data": await research_jobs.ensure_job(topic)}


@router.get("/api/manuscript/research/status")
@limiter.limit("60/minute")
async def research_status(request: Request, topic: str, current_user: dict = Depends(get_current_user)):
    from services import research_jobs

    return {"data": await research_jobs.get_job(topic)}


@router.post("/api/manuscript/research/stream")
@limiter.limit("20/minute")
async def research_stream(request: Request, payload: ResearchPayload, current_user: dict = Depends(get_current_user)):
    """SSE of the research stages for a topic, from wherever it is up to.

    Stages come off the job document, so reconnecting mid-run replays what has
    happened rather than starting from silence.
    """
    from services import research_jobs

    topic = (payload.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required.")
    await research_jobs.ensure_job(topic)
    return StreamingResponse(
        _sse_wrap(research_jobs.watch(topic), current_user["user_id"], request),
        media_type="text/event-stream",
    )


@router.post("/api/manuscript/edit")
@limiter.limit("5/minute")
async def edit_manuscript_section(request: Request, payload: ManuscriptEditPayload, current_user: dict = Depends(get_current_user)):
    provider, model = bound_requested_model(
        payload.mode, payload.provider, payload.model
    )
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
        provider,
        model,
    )
    # Flags travel with the revision so the diff view can warn *before* the user
    # accepts it, rather than leaving the previous generation's verdict on screen.
    response = {"section": payload.section, "content": content}
    response.update(flags)
    return response


@router.post("/api/manuscript/edit/stream")
@limiter.limit("5/minute")
async def edit_manuscript_section_stream(request: Request, payload: ManuscriptEditPayload, current_user: dict = Depends(get_current_user)):
    from ai.manuscript_generation import edit_section_stream
    provider, model = bound_requested_model(
        payload.mode, payload.provider, payload.model
    )
    current_provider.set(provider)
    current_model.set(model)
    gen = edit_section_stream(
        payload.topic,
        payload.section,
        payload.current_content,
        payload.instructions,
        payload.citation_style,
        payload.target_text,
        payload.target_start,
        payload.target_end,
        payload.target_kind,
        payload.mode or "manual",
        provider,
        model,
    )
    return StreamingResponse(
        _sse_wrap(gen, current_user["user_id"], request), media_type="text/event-stream"
    )

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
        # Snapshot what is about to be overwritten, not what is replacing it —
        # 1.10 is "undo a bad save", so the version list has to hold the state
        # the user wants back, and it has to be written *before* the update.
        await _snapshot_version(user_id, existing, reason="save")
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


# ─── Version history (1.10) ────────────────────────────────────────────────────
#
# Autosave writes over the draft in place, so a bad revision — an accepted diff
# the user did not mean to accept, a Continue that went off the rails, a section
# regenerated on the wrong topic — was unrecoverable. Every overwrite now leaves
# the previous state behind.
#
# Bounded per draft: a section stream can fire a dozen saves in a sitting, and an
# unbounded history of full manuscripts is the kind of collection that quietly
# becomes the biggest thing in the database.

MAX_VERSIONS_PER_DRAFT = 20


def _content_fingerprint(doc: dict) -> str:
    """Stable digest of the parts a restore would put back."""
    import hashlib

    payload = json.dumps(
        {
            "content": doc.get("content") or {},
            "gap_analysis": doc.get("gap_analysis"),
            "manuscript_refs": doc.get("manuscript_refs"),
            "citation_style": doc.get("citation_style"),
        },
        sort_keys=True, default=str,
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def _version_summary(doc: dict) -> dict:
    """Metadata a history list can render without shipping the whole draft."""
    content = doc.get("content") or {}
    sections = {
        name: len(text or "")
        for name, text in content.items()
        if isinstance(text, str)
    }
    return {
        "sections": sorted(sections),
        "section_chars": sections,
        "total_chars": sum(sections.values()),
    }


async def _snapshot_version(user_id: str, doc: dict, reason: str) -> Optional[str]:
    """Store *doc*'s current state as a version. Returns its id, or None.

    Never raises: losing a version is a worse outcome than a failed save only in
    the sense that the user does not know it happened, and a failed *save* is
    the thing they would actually notice.
    """
    content = doc.get("content") or {}
    if not content:
        return None

    fingerprint = _content_fingerprint(doc)
    try:
        versions = db["manuscript_versions"]
        latest = await versions.find_one(
            {"user_id": user_id, "manuscript_id": str(doc["_id"])},
            {"fingerprint": 1},
            sort=[("_id", -1)],
        )
        if latest and latest.get("fingerprint") == fingerprint:
            # Autosave fires on a debounce, and an identical body is not a new
            # version — it would just push a real one out of the bounded list.
            return None

        result = await versions.insert_one({
            "user_id": user_id,
            "manuscript_id": str(doc["_id"]),
            "topic": doc.get("topic"),
            "content": content,
            "gap_analysis": doc.get("gap_analysis"),
            "manuscript_refs": doc.get("manuscript_refs"),
            "citation_style": doc.get("citation_style"),
            "fingerprint": fingerprint,
            "reason": reason,
            "saved_at": doc.get("updated_at") or doc.get("created_at"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": _version_summary(doc),
        })
        await _prune_versions(user_id, str(doc["_id"]))
        return str(result.inserted_id)
    except Exception as e:
        logger.warning("Could not snapshot version for draft %s: %s", doc.get("_id"), e)
        return None


async def _prune_versions(user_id: str, manuscript_id: str) -> None:
    versions = db["manuscript_versions"]
    keep = await (
        versions.find({"user_id": user_id, "manuscript_id": manuscript_id}, {"_id": 1})
        .sort("_id", -1)
        .limit(MAX_VERSIONS_PER_DRAFT)
        .to_list(length=MAX_VERSIONS_PER_DRAFT)
    )
    if len(keep) < MAX_VERSIONS_PER_DRAFT:
        return
    oldest_kept = keep[-1]["_id"]
    await versions.delete_many({
        "user_id": user_id, "manuscript_id": manuscript_id, "_id": {"$lt": oldest_kept},
    })


@router.get("/api/manuscript/versions")
@limiter.limit("30/minute")
async def list_manuscript_versions(
    request: Request,
    current_user: dict = Depends(get_current_user),
    topic: Optional[str] = None,
    draft_id: Optional[str] = None,
    limit: int = Query(MAX_VERSIONS_PER_DRAFT, ge=1, le=100),
):
    """History for one draft, newest first. Metadata only — no section text."""
    if not draft_id and topic is None:
        raise HTTPException(status_code=400, detail="Topic or draft id is required.")
    user_id = current_user["user_id"]
    doc = await _find_user_manuscript(user_id, topic=topic, draft_id=draft_id)
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")

    docs = await (
        db["manuscript_versions"]
        .find(
            {"user_id": user_id, "manuscript_id": str(doc["_id"])},
            {"content": 0, "gap_analysis": 0, "manuscript_refs": 0, "user_id": 0},
        )
        .sort("_id", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "data": [
            {**{k: v for k, v in d.items() if k != "_id"}, "id": str(d["_id"])}
            for d in docs
        ],
        "draft_id": str(doc["_id"]),
        "max_versions": MAX_VERSIONS_PER_DRAFT,
    }


@router.get("/api/manuscript/version/{version_id}")
@limiter.limit("30/minute")
async def get_manuscript_version(
    request: Request,
    version_id: str,
    current_user: dict = Depends(get_current_user),
):
    """One version in full, for preview or diff before restoring."""
    version = await _find_user_version(current_user["user_id"], version_id)
    return {
        "data": {
            **{k: v for k, v in version.items() if k not in ("_id", "user_id")},
            "id": str(version["_id"]),
        }
    }


@router.post("/api/manuscript/restore")
@limiter.limit("10/minute")
async def restore_manuscript_version(
    request: Request,
    payload: ManuscriptRestorePayload,
    current_user: dict = Depends(get_current_user),
):
    """Put a stored version back, keeping the state it replaced as a version.

    A restore is itself an overwrite, so it snapshots first. Restoring the wrong
    version and being unable to get back is the same bug this ID exists to fix.
    """
    user_id = current_user["user_id"]
    version = await _find_user_version(user_id, payload.version_id)

    doc = await _find_user_manuscript(user_id, draft_id=version["manuscript_id"])
    if not doc:
        raise HTTPException(status_code=404, detail="The draft this version belongs to no longer exists.")

    await _snapshot_version(user_id, doc, reason="restore")

    now = datetime.now(timezone.utc).isoformat()
    await db["manuscripts"].update_one(
        {"_id": doc["_id"], "user_id": user_id},
        {"$set": {
            "content": version.get("content") or {},
            "gap_analysis": version.get("gap_analysis"),
            "manuscript_refs": version.get("manuscript_refs"),
            "citation_style": version.get("citation_style") or doc.get("citation_style") or "ieee",
            "updated_at": now,
            "restored_from": str(version["_id"]),
        }},
    )
    restored = await db["manuscripts"].find_one({"_id": doc["_id"], "user_id": user_id})
    return {
        "message": "Draft restored.",
        "restored_from": str(version["_id"]),
        "data": _public_draft(restored or {}),
    }


async def _find_user_version(user_id: str, version_id: str) -> dict:
    try:
        oid = ObjectId(version_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid version id.")
    # Scoped by user_id, not just id: version ids are ObjectIds and therefore
    # enumerable, and a version holds the full text of somebody's manuscript.
    version = await db["manuscript_versions"].find_one({"_id": oid, "user_id": user_id})
    if not version:
        raise HTTPException(status_code=404, detail="Version not found.")
    return version


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

    Tries the topic the draft is actually stored under before the one the client
    sent. `_find_user_manuscript` accepts a whitespace-drifted topic, so an exact
    match here on the raw payload missed a snapshot that existed and reported the
    draft as having no reference set at all.
    """
    candidates = []
    for candidate in (doc.get("topic"), topic):
        for variant in (candidate, (candidate or "").strip()):
            if variant and variant not in candidates:
                candidates.append(variant)

    snapshot_doc = None
    for variant in candidates:
        try:
            snapshot_doc = await db["manuscript_references"].find_one(
                {"user_id": user_id, "topic": variant}, {"_id": 0, "references": 1}
            )
        except Exception as e:
            logger.warning(f"Reference snapshot lookup failed for '{variant}': {e}")
            snapshot_doc = None
        if (snapshot_doc or {}).get("references"):
            if variant != topic:
                logger.info(f"Reference snapshot for '{topic}' found under '{variant}'")
            break

    references = (snapshot_doc or {}).get("references") or []
    if references:
        return references

    legacy = _flatten_references(doc.get("manuscript_refs"))
    if legacy:
        logger.info(f"Export for '{topic}' fell back to manuscript_refs ({len(legacy)} refs)")
        return legacy

    rebuilt = await _rebuild_snapshot_from_literature(
        user_id, topic, candidates, doc.get("manuscript_refs"), doc.get("citation_style") or "ieee"
    )
    if rebuilt:
        return rebuilt
    return []


async def _rebuild_snapshot_from_literature(user_id: str, topic: str, topic_candidates: list,
                                            manuscript_refs, citation_style: str) -> list:
    """L14: rebuild manuscript_references from display refs + a saved survey."""
    if not manuscript_refs:
        return []
    papers = []
    for variant in topic_candidates:
        try:
            survey = await db["literature"].find_one(
                {"user_id": user_id, "query": variant}, {"_id": 0, "papers": 1}
            )
        except Exception as e:
            logger.warning(f"Literature lookup for snapshot rebuild failed: {e}")
            survey = None
        if survey and survey.get("papers"):
            papers = survey["papers"]
            break
    snapshot = match_manuscript_refs_to_papers(manuscript_refs, papers, citation_style)
    if not snapshot:
        return []
    store_topic = (topic_candidates[0] if topic_candidates else topic) or topic
    try:
        await db["manuscript_references"].update_one(
            {"user_id": user_id, "topic": store_topic},
            {"$set": {
                "user_id": user_id,
                "topic": store_topic,
                "references": snapshot,
                "rebuilt_from_literature": True,
            }},
            upsert=True,
        )
        logger.info(f"Rebuilt reference snapshot for '{store_topic}' ({len(snapshot)} refs) from literature")
    except Exception as e:
        logger.warning(f"Could not persist rebuilt snapshot for '{store_topic}': {e}")
    return snapshot


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
            keywords=payload.keywords or "",
            manuscript_refs=doc.get("manuscript_refs"),
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
    # Claim \cite and the citation map only when they are actually in the file.
    # The README used to promise both unconditionally, which read as reassurance
    # on exactly the export that had neither (audit L10).
    if "\\cite{" in tex:
        readme += (
            f"\nReferences: {len(references)} entries in references.bib, cited with \\cite{{}}.\n"
            "A citation map is included as comments at the top of paper.tex -- worth a\n"
            "30-second read to confirm [1] is the paper you expect.\n"
        )
    else:
        readme += (
            f"\nReferences: {len(references)} entries in references.bib. The draft's prose\n"
            "contains no [N] citation markers, so no \\cite commands were emitted and\n"
            "BibTeX will print an empty bibliography.\n"
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
async def list_manuscript_drafts(
    request: Request,
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
):
    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    query = {"user_id": user_id}
    if cursor:
        try:
            query["_id"] = {"$lt": ObjectId(cursor)}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor.")
    docs = await (
        collection.find(query, {"user_id": 0, "content": 0})
        .sort("_id", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    drafts = [_public_draft(doc) for doc in docs]
    next_cursor = str(docs[-1]["_id"]) if len(docs) == limit else None
    return {"data": drafts, "next_cursor": next_cursor}


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

    try:
        await db["manuscript_versions"].delete_many(
            {"user_id": user_id, "manuscript_id": str(doc["_id"])}
        )
    except Exception as e:
        logger.warning("Failed to delete version history for draft %s: %s", doc["_id"], e)

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
# An LLM call per request; it was the only generation route with no budget.
@limiter.limit("10/minute")
async def get_venues(request: Request, payload: VenuePayload, current_user: dict = Depends(get_current_user)):
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
@limiter.limit("10/minute")
async def get_guidelines(request: Request, payload: GuidelinePayload, current_user: dict = Depends(get_current_user)):
    if not payload.venue.get("name"):
        raise HTTPException(status_code=400, detail="Venue name is required.")
    result = await align_guidelines(payload.manuscript, payload.venue)
    return {"data": result}
