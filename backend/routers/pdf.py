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

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ai.pdf_analysis import analyze_uploaded_paper, extract_pdf_structure, extract_pdf_text
from ai.source_ingestion import extract_source_text
from core.auth import get_current_user
from core.limiter import limiter
from core.database import db, get_pdf_bucket
from core.file_validation import verify_pdf_signature, verify_upload_signature
from schemas import PdfChatSavePayload
from core.ssrf_guard import assert_public_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pdf"])


@router.post("/api/manuscript/extract-pdf")
@limiter.limit("10/minute")
async def extract_pdf_endpoint(request: Request, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="File too large. Limit is 10MB.")
    verify_pdf_signature(contents)

    text, structure = await asyncio.gather(
        extract_pdf_text(contents),
        extract_pdf_structure(contents)
    )

    file_id = await get_pdf_bucket().upload_from_stream(
        file.filename,
        contents,
        metadata={"user_id": str(current_user["user_id"]), "content_type": "application/pdf"}
    )

    return {"text": text, "structure": structure, "file_id": str(file_id)}

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
        assert_public_url(url_str)
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, max_redirects=0) as client:
                resp = await client.get(url_str)
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
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch content from URL: {e}")
    elif file:
        raw = await file.read()
        verify_upload_signature(raw, file.content_type, file.filename)
        text = await extract_source_text(raw, file.content_type, file.filename)
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
    try:
        oid = ObjectId(file_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file_id")

    grid_out = await get_pdf_bucket().open_download_stream(oid)
    if grid_out.metadata.get("user_id") != str(current_user["user_id"]):
        raise HTTPException(status_code=403, detail="Not your file")

    async def stream():
        while True:
            chunk = await grid_out.readchunk()
            if not chunk:
                break
            yield chunk

    return StreamingResponse(stream(), media_type="application/pdf")


@router.post("/api/manuscript/analyze-pdf")
@limiter.limit("5/minute")
async def analyze_pdf_endpoint(
    request: Request,
    text: str = Form(...),
    structure: Optional[str] = Form(None),
    custom_prompt: Optional[str] = Form(None),
    chat_id: Optional[str] = Form(None),
    history: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    struct_dict = json.loads(structure) if structure else None
    hist_list = json.loads(history) if history else []
    result = await analyze_uploaded_paper(text, custom_prompt, struct_dict, hist_list, chat_id)
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
async def list_pdf_chats(request: Request, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["pdf_chats"]
    # only return metadata, not full text or messages
    cursor = collection.find(
        {"user_id": user_id},
        {"text": 0, "structure": 0, "messages": 0, "user_id": 0}
    ).sort("updated_at", -1)

    chats = []
    async for doc in cursor:
        doc["chat_id"] = str(doc.pop("_id"))
        chats.append(doc)
    return {"data": chats}

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
