"""PDF and source-document handling: upload, extraction, streaming back a stored
PDF, the analysis chat, and its saved history.

Route order matters here — ``/api/pdf-chats/list`` must stay declared before
``/api/pdf-chats/{chat_id}`` or the literal path gets swallowed by the param one.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ai.pdf_analysis import analyze_uploaded_paper, extract_pdf_structure, extract_pdf_text
from ai.source_ingestion import extract_source_text
from core.auth import get_current_user
from core.config import MAX_UPLOAD_BYTES, MAX_URL_FETCH_BYTES
from core.limiter import limiter
from core.database import db
from core.file_validation import read_upload_capped, verify_pdf_signature, verify_upload_signature
from core.object_store import (
    PdfNotFound,
    content_digest,
    find_stored_pdf,
    open_pdf,
    owner_of,
    remember_stored_pdf,
    store_pdf,
)
from core.processing_consent import consent_state, record_consent, user_allows_third_party
from core.saved_lists import (
    LIST_SORT,
    cursor_filter,
    meta_update,
    meta_view,
    next_cursor,
    parse_object_id,
)
from pymongo import ReturnDocument
from schemas import PdfChatSavePayload, ProcessingConsentPayload, SavedItemPatch
from core.ssrf_guard import safe_fetch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pdf"])


async def _load_owned_chat(chat_id: str, user_id: str) -> dict:
    """Load a pdf_chats document the caller owns, or 400/404.

    Everything derived from a chat id is a server-side cache scope — the rolling
    conversation summary and the Gemini context cache. Both used to be keyed on
    the raw id straight off the request body, so passing somebody else's id (a
    24-hex ObjectId, guessable in bulk) returned their paper's cached context.
    """
    try:
        oid = ObjectId(chat_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chat ID.")
    doc = await db["pdf_chats"].find_one({"_id": oid, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return doc


def _history_from_stored_messages(messages: list) -> list:
    """Same shape the frontend used to POST as `history` on every turn."""
    out = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("isLoading") or msg.get("error"):
            continue
        role = msg.get("role") or "assistant"
        data = msg.get("data") if isinstance(msg.get("data"), dict) else None
        if data and msg.get("type") in ("structured", "gap_analysis"):
            gaps = "; ".join(data.get("gaps") or [])
            covered = "; ".join(data.get("well_covered") or [])
            direction = data.get("suggested_direction") or ""
            content = (
                f"[Gap analysis]\nWell covered: {covered}\nGaps: {gaps}\nSuggested: {direction}"
            )
        else:
            content = msg.get("content") or ""
        if str(content).strip():
            out.append({"role": role, "content": content})
    return out


@router.post("/api/manuscript/extract-pdf")
@limiter.limit("10/minute")
async def extract_pdf_endpoint(request: Request, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    contents = await read_upload_capped(file, MAX_UPLOAD_BYTES)
    verify_pdf_signature(contents)

    # The same bytes uploaded again (PDF-4): reuse the stored file, and if a chat
    # already holds it, hand that chat back instead of extracting the paper a
    # second time and opening a duplicate. The client opens `existing_chat_id`.
    user_id = current_user["user_id"]
    digest = content_digest(contents)
    file_id = await find_stored_pdf(str(user_id), digest)
    if file_id:
        existing = await db["pdf_chats"].find_one(
            {"user_id": user_id, "file_id": file_id},
            {"text": 1, "structure": 1},
            sort=[("updated_at", -1)],
        )
        if existing:
            return {
                "text": existing.get("text") or "",
                "structure": existing.get("structure"),
                "file_id": file_id,
                "existing_chat_id": str(existing["_id"]),
                "reused": True,
            }

    # Whether this file may be sent to LlamaCloud (1.16). Resolved per upload,
    # not cached on the client, so revoking consent takes effect on the very
    # next file rather than whenever the page is next reloaded.
    allow_third_party = await user_allows_third_party(current_user["user_id"])

    text, structure = await asyncio.gather(
        extract_pdf_text(contents, allow_third_party=allow_third_party),
        extract_pdf_structure(contents)
    )

    # S3 when S3_BUCKET is set, GridFS otherwise (AWS-6). The id shape differs
    # between the two, which is why nothing downstream may parse it — it is
    # handed back to `open_pdf` exactly as stored.
    if not file_id:
        file_id = await store_pdf(str(user_id), file.filename, contents)
        await remember_stored_pdf(str(user_id), digest, file_id)

    return {
        "text": text,
        "structure": structure,
        "file_id": file_id,
        # Told, not implied: the client shows which parser handled the file.
        "parsed_by": "third_party" if allow_third_party else "local",
    }


@router.get("/api/user/processing-consent")
@limiter.limit("30/minute")
async def get_processing_consent(request: Request, current_user: dict = Depends(get_current_user)):
    """What the user has been told about third-party processing, and their answer."""
    from core.auth import _load_user

    return consent_state(await _load_user(current_user["user_id"]))


@router.post("/api/user/processing-consent")
@limiter.limit("10/minute")
async def set_processing_consent(
    request: Request,
    payload: ProcessingConsentPayload,
    current_user: dict = Depends(get_current_user),
):
    return await record_consent(current_user["user_id"], payload.granted)

@router.post("/api/sources/upload")
@limiter.limit("10/minute")
async def upload_source(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    topic: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    if url and url.strip():
        url_str = url.strip()
        try:
            # Pins the resolved address and re-validates each redirect hop, so a
            # rebinding record or a `302` into the metadata service cannot slip
            # past the check the way a bare hostname validation let them.
            resp = await safe_fetch(url_str, max_bytes=MAX_URL_FETCH_BYTES, timeout=10.0)
            resp.raise_for_status()
            html_text = resp.text
            try:
                import importlib
                trafilatura = importlib.import_module("trafilatura")
                extracted = trafilatura.extract(html_text)
                text = extracted if extracted else html_text
            except Exception:
                import re
                text = re.sub(r'<[^>]+>', ' ', html_text)
                text = ' '.join(text.split())
            filename = url_str.split("//")[-1].split("/")[0] or url_str
            content_type = "url"
        except HTTPException:
            # Already a deliberate, safe message (blocked target, too large).
            raise
        except Exception as e:
            logger.info("Source URL fetch failed for %s: %s", url_str, e)
            raise HTTPException(status_code=400, detail="Failed to fetch content from that URL.")
    elif file:
        raw = await read_upload_capped(file, MAX_UPLOAD_BYTES)
        verify_upload_signature(raw, file.content_type, file.filename)
        text = await extract_source_text(
            raw, file.content_type, file.filename,
            allow_third_party=await user_allows_third_party(user_id),
        )
        filename = file.filename
        content_type = file.content_type
    else:
        raise HTTPException(status_code=400, detail="Either file or url must be provided")

    doc = {
        "user_id": user_id,
        "topic": topic.strip().lower(),
        "filename": filename,
        "type": content_type,
        "raw_text": text,
        "created_at": datetime.now(timezone.utc)
    }
    result = await db["sources"].insert_one(doc)
    return {"id": str(result.inserted_id), "filename": filename, "type": content_type}

@router.get("/api/sources")
@limiter.limit("30/minute")
async def list_sources(request: Request, topic: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    cursor = db["sources"].find({"user_id": user_id, "topic": topic.strip().lower()}, {"raw_text": 0})
    sources = []
    async for s in cursor:
        s["_id"] = str(s["_id"])
        s["id"] = str(s["_id"])
        sources.append(s)
    return sources

@router.delete("/api/sources/{source_id}")
@limiter.limit("20/minute")
async def delete_source(request: Request, source_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    try:
        await db["sources"].delete_one({"_id": ObjectId(source_id), "user_id": user_id})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid source_id")
    return {"ok": True}


@router.get("/api/manuscript/pdf/{file_id}")
@limiter.limit("30/minute")
async def get_pdf(request: Request, file_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["user_id"])

    # An S3 key carries its owner, so a request for somebody else's file is
    # refused here without spending a read on it. GridFS ids carry nothing, so
    # they fall through to the check below.
    claimed_owner = owner_of(file_id)
    if claimed_owner is not None and claimed_owner != user_id:
        raise HTTPException(status_code=403, detail="Not your file")

    try:
        owner, chunks = await open_pdf(file_id)
    except PdfNotFound:
        # Malformed and missing are one answer on purpose: distinguishing them
        # tells an enumerator which ids are real.
        raise HTTPException(status_code=404, detail="File not found")

    # A GridFS file written without metadata (or by an older code path) reports
    # `None` for its owner. No recorded owner means nobody can claim it, so this
    # comparison has to fail closed rather than raise.
    if owner != user_id:
        raise HTTPException(status_code=403, detail="Not your file")

    return StreamingResponse(chunks, media_type="application/pdf")


@router.post("/api/manuscript/analyze-pdf")
@limiter.limit("5/minute")
async def analyze_pdf_endpoint(
    request: Request,
    text: Optional[str] = Form(None),
    structure: Optional[str] = Form(None),
    custom_prompt: Optional[str] = Form(None),
    chat_id: Optional[str] = Form(None),
    history: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    try:
        struct_dict = json.loads(structure) if structure else None
        hist_list = json.loads(history) if history else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="structure and history must be valid JSON.")

    # The cache scope is built here, from an id the server has confirmed this
    # caller owns — never from the raw request field. `chat_id` alone was enough
    # to address another user's rolling summary and Gemini context cache.
    cache_scope = None
    if chat_id:
        doc = await _load_owned_chat(chat_id, str(current_user["user_id"]))
        cache_scope = f"{current_user['user_id']}:{chat_id}"
        # Follow-up turns send the id, not megabytes of paper text. Fill any
        # omitted fields from the saved chat the caller already owns.
        if not (text and str(text).strip()):
            text = doc.get("text") or ""
        if struct_dict is None and doc.get("structure") is not None:
            struct_dict = doc["structure"]
        if not hist_list:
            hist_list = _history_from_stored_messages(doc.get("messages") or [])
    elif not (text and str(text).strip()):
        raise HTTPException(status_code=400, detail="text is required when chat_id is not set.")

    result = await analyze_uploaded_paper(text, custom_prompt, struct_dict, hist_list, cache_scope)
    return result


# ─── PDF Chat History ──────────────────────────────────────────────────────────

@router.post("/api/pdf-chats/save")
@limiter.limit("20/minute")
async def save_pdf_chat(request: Request, payload: PdfChatSavePayload, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["pdf_chats"]
    now = datetime.now(timezone.utc).isoformat()

    doc = {
        "user_id": user_id,
        "filename": payload.filename,
        "text": payload.text,
        "structure": payload.structure,
        "messages": payload.messages,
        "file_id": payload.file_id,
        "updated_at": now
    }

    if payload.chat_id:
        try:
            obj_id = ObjectId(payload.chat_id)
            await collection.update_one({"_id": obj_id, "user_id": user_id}, {"$set": doc})
            return {"message": "Chat updated", "chat_id": payload.chat_id}
        except Exception:
            pass # fallback to insert if invalid chat_id

    doc["created_at"] = now
    result = await collection.insert_one(doc)
    return {"message": "Chat saved", "chat_id": str(result.inserted_id)}

@router.get("/api/pdf-chats/list")
@limiter.limit("30/minute")
async def list_pdf_chats(
    request: Request,
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
):
    user_id = current_user["user_id"]
    collection = db["pdf_chats"]
    query = {"user_id": user_id, **cursor_filter(cursor)}
    docs = await (
        collection.find(
            query,
            {"text": 0, "structure": 0, "messages": 0, "user_id": 0},
        )
        .sort(LIST_SORT)
        .limit(limit)
        .to_list(length=limit)
    )
    page_cursor = next_cursor(docs, limit)
    chats = []
    for doc in docs:
        doc["chat_id"] = str(doc.pop("_id"))
        chats.append(doc)
    return {"data": chats, "next_cursor": page_cursor}

@router.get("/api/pdf-chats/{chat_id}")
@limiter.limit("30/minute")
async def load_pdf_chat(request: Request, chat_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["pdf_chats"]
    try:
        doc = await collection.find_one({"_id": ObjectId(chat_id), "user_id": user_id}, {"user_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Chat not found.")
        doc["chat_id"] = str(doc.pop("_id"))
        return {"data": doc}
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid chat ID.")

@router.patch("/api/pdf-chats/{chat_id}")
@limiter.limit("30/minute")
async def update_pdf_chat_meta(request: Request, chat_id: str, payload: SavedItemPatch, current_user: dict = Depends(get_current_user)):
    """Pin or rename a chat (ROW-1). The title is display-only; `filename` stays."""
    doc = await db["pdf_chats"].find_one_and_update(
        {"_id": parse_object_id(chat_id, "Chat"), "user_id": current_user["user_id"]},
        meta_update(payload.pinned, payload.title),
        projection={"pinned": 1, "title": 1},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return {"chat_id": chat_id, **meta_view(doc)}

@router.delete("/api/pdf-chats/{chat_id}")
@limiter.limit("20/minute")
async def delete_pdf_chat(request: Request, chat_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["pdf_chats"]
    try:
        result = await collection.delete_one({"_id": ObjectId(chat_id), "user_id": user_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Chat not found.")
        return {"message": "Chat deleted"}
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid chat ID.")
