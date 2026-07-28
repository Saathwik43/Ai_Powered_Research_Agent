from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
import logging
import asyncio
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from fastapi.responses import JSONResponse
import time
import httpx
from bson import ObjectId
import traceback

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from auth import decode_access_token
from fastapi import Request

from ai.topic_discovery import discover_topics
from ai.manuscript_generation import generate_section, edit_section
from ai.gap_analysis import analyze_gaps
from ai.venue_recommendation import recommend_venues
from ai.guideline_alignment import align_guidelines
from ai.pdf_analysis import extract_pdf_text, extract_pdf_structure, analyze_uploaded_paper
from ai.source_ingestion import extract_source_text
from integrations.paper_search import search_all
from ai.relevance import _filter_relevant_papers
from integrations.arxiv import fetch_category_feed, fetch_multiple_feeds, CATEGORY_MAP
from integrations.crossref import search_journals
from integrations.github_knowledge import (
    sync_repository, sync_all_repositories,
    list_categories, list_all_repos,
    find_papers_by_category, search_github_knowledge
)

from database import db, ping_db, ensure_indexes, get_pdf_bucket
from auth import signup_user, login_user, get_current_user, seed_admin, verify_google_token, google_auth_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", filename="backend.log")
logger = logging.getLogger(__name__)

def get_user_id_for_rate_limit(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            payload = decode_access_token(token)
            user_id = payload.get("sub")
            if user_id:
                return user_id
        except Exception:
            pass
    return get_remote_address(request)

limiter = Limiter(key_func=get_user_id_for_rate_limit)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ping_db()
    await ensure_indexes()
    await seed_admin()
    yield

app = FastAPI(title="AI-Powered Research Paper Publishing Agent", lifespan=lifespan, debug=False)

import os

cors_origins_env = os.getenv("CORS_ORIGINS")
if cors_origins_env:
    origins = cors_origins_env.split(",")
else:
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})

# ─── Pydantic Models ───────────────────────────────────────────────────────────

class SignupPayload(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    name: str = Field(..., min_length=1)

class LoginPayload(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class GoogleAuthPayload(BaseModel):
    token: str = Field(..., min_length=1)

class ManuscriptPayload(BaseModel):
    topic: str
    section: str = "abstract"
    context: str = ""
    citation_style: str = "ieee"

class ManuscriptStreamPayload(ManuscriptPayload):
    mode: str = "manual"
    provider: str = None
    model: str = None

class GapAnalysisPayload(BaseModel):
    topic: str

class ManuscriptEditPayload(BaseModel):
    topic: str
    section: str = "abstract"
    current_content: str
    instructions: str

class ManuscriptSavePayload(BaseModel):
    topic: str
    content: Dict[str, Any]
    gap_analysis: Optional[Dict[str, Any]] = None
    manuscript_refs: Optional[Dict[str, Any]] = None
    citation_style: Optional[str] = "ieee"

class PdfAnalyzePayload(BaseModel):
    text: str
    custom_prompt: Optional[str] = None

class PdfChatSavePayload(BaseModel):
    chat_id: Optional[str] = None
    filename: str
    text: str
    structure: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]]
    file_id: Optional[str] = None

class VenuePayload(BaseModel):
    abstract: str = ""
    domain: str = ""

class GuidelinePayload(BaseModel):
    manuscript: Dict[str, Any]
    venue: Dict[str, Any]

class LiteratureSavePayload(BaseModel):
    query: str
    papers: List[Any]

class GithubSyncPayload(BaseModel):
    repo: Optional[str] = None

# ─── Root ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "Welcome to the Research Agent API"}


# ─── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/signup")
@limiter.limit("5/minute")
async def signup(request: Request, payload: SignupPayload):
    email = payload.email.strip().lower()
    return await signup_user(email, payload.password, payload.name.strip())

@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginPayload):
    email = payload.email.strip().lower()
    return await login_user(email, payload.password)

@app.post("/api/auth/google")
@limiter.limit("10/minute")
async def google_auth(request: Request, payload: GoogleAuthPayload):
    idinfo = verify_google_token(payload.token)
    email = idinfo.get('email')
    name = idinfo.get('name', 'Google User')
    picture = idinfo.get('picture')
    if not email:
        raise HTTPException(status_code=400, detail="Google token does not contain an email.")
    return await google_auth_user(email.lower(), name, picture)


@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


# ─── Topic Discovery ───────────────────────────────────────────────────────────

@app.get("/api/topics")
async def get_topics(intent: str, current_user: dict = Depends(get_current_user)):
    result = await discover_topics(intent)
    return result


# ─── Literature — Unified Search (OpenAlex + arXiv + GitHub) ──────────────────

@app.get("/api/literature")
@limiter.limit("5/minute")
async def get_literature(request: Request, query: str, current_user: dict = Depends(get_current_user)):
    """
    Unified literature search across OpenAlex, arXiv, and GitHub knowledge bases.
    Applies the shared relevance filter used by manuscript generation so noisy
    cross-domain results do not leak into the literature view.
    """
    # Ask for 20 per source, yielding up to 180 total before deduplication
    papers = await search_all(query, limit_per_source=20)
    total = len(papers)
    filtered = await _filter_relevant_papers(query, papers)
    return {"data": filtered, "count": len(filtered), "total": total, "has_more": False}


# ─── arXiv — Keyword Search ────────────────────────────────────────────────────

@app.get("/api/arxiv/search")
async def arxiv_search_endpoint(query: str, limit: int = 10, current_user: dict = Depends(get_current_user)):
    """Search arXiv directly by keyword."""
    from integrations.arxiv import search_papers as arxiv_search
    papers = await arxiv_search(query, limit=limit)
    return {"data": papers, "count": len(papers)}


# ─── arXiv — Category RSS Feed ────────────────────────────────────────────────

@app.get("/api/arxiv/feed")
async def arxiv_feed(category: str = "cs.AI", limit: int = 10, current_user: dict = Depends(get_current_user)):
    """
    Fetch latest papers from an arXiv RSS category feed.
    category: arXiv code e.g. cs.AI, cs.LG, cs.CR, cs.CV, cs.CL, quant-ph, q-bio.GN
    """
    papers = await fetch_category_feed(category, limit=limit)
    return {"data": papers, "category": category, "count": len(papers)}


@app.get("/api/arxiv/trending")
async def arxiv_trending(current_user: dict = Depends(get_current_user)):
    """
    Fetch latest papers from multiple arXiv categories at once for the dashboard.
    Returns a dict keyed by category code.
    """
    categories = ["cs.AI", "cs.LG", "cs.CR", "cs.CV", "cs.CL", "quant-ph"]
    import asyncio
    feeds = await asyncio.gather(*[fetch_category_feed(c, limit=5) for c in categories])
    result = {}
    for cat, papers in zip(categories, feeds):
        result[cat] = papers
    return {"data": result}


# ─── Crossref Journal Search ───────────────────────────────────────────────────

@app.get("/api/crossref-journals")
async def get_crossref_journals(query: str, current_user: dict = Depends(get_current_user)):
    journals = await search_journals(query)
    formatted = []
    for j in journals:
        formatted.append({
            "title": j.get("title", ["Unknown"])[0] if isinstance(j.get("title"), list) else j.get("title", "Unknown"),
            "publisher": j.get("publisher", "Unknown"),
            "issn": j.get("ISSN", []),
            "subjects": [s.get("name", "") for s in j.get("subjects", [])],
        })
    return {"data": formatted}


# ─── GitHub Knowledge Base ─────────────────────────────────────────────────────

@app.get("/api/github/repos")
async def get_github_repos(current_user: dict = Depends(get_current_user)):
    """List all configured GitHub knowledge repos and their sync status."""
    return {"data": list_all_repos()}


_sync_lock = asyncio.Lock()

@app.post("/api/github/sync")
async def sync_github(payload: GithubSyncPayload, current_user: dict = Depends(get_current_user)):
    """
    Sync one or all GitHub repos.
    """
    if _sync_lock.locked():
        raise HTTPException(status_code=409, detail="Sync already in progress")
    
    async with _sync_lock:
        repo_name = payload.repo
        if repo_name:
            success = await asyncio.to_thread(sync_repository, repo_name)
            return {"message": f"{'Synced' if success else 'Failed'}: {repo_name}", "success": success}
        else:
            results = await asyncio.to_thread(sync_all_repositories)
            return {"message": "Sync complete.", "results": results}


@app.get("/api/github/categories")
async def get_github_categories(repo: str = "papers-we-love", current_user: dict = Depends(get_current_user)):
    """List categories in a specific GitHub repo."""
    import asyncio
    cats = await asyncio.to_thread(list_categories, repo)
    if not cats:
        return {"data": [], "message": f"Repo '{repo}' not synced yet. POST /api/github/sync first."}
    return {"data": cats}


@app.get("/api/github/papers")
async def get_github_papers(repo: str = "papers-we-love", category: str = "", current_user: dict = Depends(get_current_user)):
    """List papers in a category of a GitHub repo."""
    import asyncio
    papers = await asyncio.to_thread(find_papers_by_category, category, repo)
    return {"data": papers, "count": len(papers)}


@app.get("/api/github/search")
async def search_github(query: str, current_user: dict = Depends(get_current_user)):
    """Search all synced GitHub repos for papers matching the query."""
    import asyncio
    results = await asyncio.to_thread(search_github_knowledge, query)
    return {"data": results, "count": len(results)}


# ─── Manuscript Generation ─────────────────────────────────────────────────────

@app.post("/api/manuscript")
@limiter.limit("5/minute")
async def draft_manuscript(request: Request, payload: ManuscriptPayload, current_user: dict = Depends(get_current_user)):
    from ai.manuscript_generation import generate_section
    content, flags = await generate_section(payload.topic, payload.section, payload.context, payload.citation_style)
    if '{"error": "topic_unclear"}' in content:
        raise HTTPException(status_code=400, detail="The provided topic is unclear or appears to be nonsense.")
    
    response = {"section": payload.section, "content": content}
    response.update(flags)
    return response

from fastapi.responses import StreamingResponse
import json
from ai.llm_provider import current_provider, current_model

async def _sse_wrap(generator):
    async for chunk in generator:
        yield f"data: {json.dumps(chunk)}\n\n"

@app.post("/api/manuscript/stream")
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

@app.post("/api/manuscript/edit")
@limiter.limit("5/minute")
async def edit_manuscript_section(request: Request, payload: ManuscriptEditPayload, current_user: dict = Depends(get_current_user)):
    content = await edit_section(payload.topic, payload.section, payload.current_content, payload.instructions)
    return {"section": payload.section, "content": content}

@app.post("/api/manuscript/save")
async def save_manuscript_draft(payload: ManuscriptSavePayload, current_user: dict = Depends(get_current_user)):
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


@app.get("/api/manuscript/load")
async def load_manuscript_draft(topic: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    doc = await collection.find_one({"user_id": user_id, "topic": topic}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No draft found for this topic.")
    return {"data": doc}


@app.get("/api/manuscript/list")
async def list_manuscript_drafts(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    cursor = collection.find(
        {"user_id": user_id},
        {"_id": 0, "user_id": 0, "content": 0}
    ).sort("updated_at", -1)
    drafts = [doc async for doc in cursor]
    return {"data": drafts}


@app.post("/api/manuscript/extract-pdf")
@limiter.limit("10/minute")
async def extract_pdf_endpoint(request: Request, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="File too large. Limit is 10MB.")

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

@app.post("/api/sources/upload")
async def upload_source(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    topic: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    if url and url.strip():
        url_str = url.strip()
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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
        text = await extract_source_text(raw, file.content_type, file.filename)
        filename = file.filename
        content_type = file.content_type
    else:
        raise HTTPException(status_code=400, detail="Either file or url must be provided")

    doc = {
        "user_id": user_id,
        "topic": topic,
        "filename": filename,
        "type": content_type,
        "raw_text": text,
        "created_at": datetime.now(timezone.utc)
    }
    result = await db["sources"].insert_one(doc)
    return {"id": str(result.inserted_id), "filename": filename, "type": content_type}

@app.get("/api/sources")
async def list_sources(topic: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    cursor = db["sources"].find({"user_id": user_id, "topic": topic})
    sources = []
    async for s in cursor:
        s["_id"] = str(s["_id"])
        s["id"] = str(s["_id"])
        sources.append(s)
    return sources

@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    try:
        await db["sources"].delete_one({"_id": ObjectId(source_id), "user_id": user_id})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid source_id")
    return {"ok": True}

    
@app.get("/api/manuscript/pdf/{file_id}")
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


@app.post("/api/manuscript/analyze-pdf")
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
    import json
    struct_dict = json.loads(structure) if structure else None
    hist_list = json.loads(history) if history else []
    result = await analyze_uploaded_paper(text, custom_prompt, struct_dict, hist_list, chat_id)
    return result


# ─── Venue Recommendations ─────────────────────────────────────────────────────

@app.post("/api/venues")
async def get_venues(payload: VenuePayload, current_user: dict = Depends(get_current_user)):
    result = await recommend_venues(payload.abstract, payload.domain)
    return result

@app.post("/api/gap-analysis")
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

@app.post("/api/guidelines")
async def get_guidelines(payload: GuidelinePayload, current_user: dict = Depends(get_current_user)):
    if not payload.venue.get("name"):
        raise HTTPException(status_code=400, detail="Venue name is required.")
    result = await align_guidelines(payload.manuscript, payload.venue)
    return {"data": result}


# ─── Save / Load Literature Survey (per user) ─────────────────────────────────

@app.post("/api/literature/save")
async def save_literature(payload: LiteratureSavePayload, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    existing = await collection.find_one({"user_id": user_id, "query": payload.query})
    if existing:
        await collection.update_one({"user_id": user_id, "query": payload.query}, {"$set": {"papers": payload.papers}})
        return {"message": "Literature survey updated.", "query": payload.query}
    else:
        await collection.insert_one({"user_id": user_id, "query": payload.query, "papers": payload.papers})
        return {"message": "Literature survey saved.", "query": payload.query}


@app.get("/api/literature/load")
async def load_literature(query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    doc = await collection.find_one({"user_id": user_id, "query": query}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No saved survey found for this query.")
    return {"data": doc}


@app.get("/api/literature/list")
async def list_literature_surveys(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    cursor = collection.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).sort("_id", -1)
    surveys = [doc async for doc in cursor]
    return {"data": surveys}

@app.delete("/api/literature/delete/{query}")
async def delete_literature(query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    result = await collection.delete_one({"user_id": user_id, "query": query})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Survey not found.")
    return {"message": "Literature survey deleted successfully."}


# ─── PDF Chat History ──────────────────────────────────────────────────────────


@app.post("/api/pdf-chats/save")
async def save_pdf_chat(payload: PdfChatSavePayload, current_user: dict = Depends(get_current_user)):
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

@app.get("/api/pdf-chats/list")
async def list_pdf_chats(current_user: dict = Depends(get_current_user)):
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

@app.get("/api/pdf-chats/{chat_id}")
async def load_pdf_chat(chat_id: str, current_user: dict = Depends(get_current_user)):
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

@app.delete("/api/pdf-chats/{chat_id}")
async def delete_pdf_chat(chat_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["pdf_chats"]
    try:
        result = await collection.delete_one({"_id": ObjectId(chat_id), "user_id": user_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Chat not found.")
        return {"message": "Chat deleted"}
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid chat ID.")

from usage_tracker import get_user_usage, DAILY_TOKEN_QUOTA, TOKENS_PER_MESSAGE

@app.get("/api/user/usage")
async def get_my_usage(current_user: dict = Depends(get_current_user)):
    return await get_user_usage(current_user["user_id"])

@app.get("/api/admin/usage")
async def admin_usage_endpoint(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    collection = db["usage_logs"]
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    pipeline = [
        {"$match": {"date": today}},
        {"$group": {"_id": "$user_id", "total_tokens": {"$sum": "$tokens"}}}
    ]
    cursor = collection.aggregate(pipeline)
    usage = await cursor.to_list(length=1000)
    
    users_collection = db["users"]
    
    results = []
    for u in usage:
        user_id = u["_id"]
        total_tokens = u["total_tokens"]
        try:
            user_doc = await users_collection.find_one({"_id": ObjectId(user_id)})
        except Exception:
            user_doc = None
            
        email = user_doc["email"] if user_doc else user_id
        custom_q = user_doc.get("custom_quota") if user_doc else None
        effective_quota = int(custom_q) if custom_q is not None else DAILY_TOKEN_QUOTA
        
        messages_left = max(0.0, (effective_quota - total_tokens) / TOKENS_PER_MESSAGE)
        results.append({
            "user_id": user_id,
            "email": email,
            "used": total_tokens,
            "messages_left": round(messages_left, 1),
            "quota": effective_quota
        })
        
    return {"data": results}

@app.get("/api/admin/users")
async def admin_get_all_users(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    users_cursor = db["users"].find({})
    user_docs = await users_cursor.to_list(length=1000)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    results = []
    for u in user_docs:
        uid_str = str(u["_id"])
        
        # Calculate today's tokens
        today_pipeline = [
            {"$match": {"user_id": uid_str, "date": today}},
            {"$group": {"_id": None, "total": {"$sum": "$tokens"}}}
        ]
        t_res = await db["usage_logs"].aggregate(today_pipeline).to_list(length=1)
        tokens_today = t_res[0]["total"] if t_res else 0
        
        # Calculate lifetime tokens
        life_pipeline = [
            {"$match": {"user_id": uid_str}},
            {"$group": {"_id": None, "total": {"$sum": "$tokens"}}}
        ]
        l_res = await db["usage_logs"].aggregate(life_pipeline).to_list(length=1)
        tokens_total = l_res[0]["total"] if l_res else 0
        
        custom_q = u.get("custom_quota")
        effective_quota = int(custom_q) if custom_q is not None else DAILY_TOKEN_QUOTA
        messages_left = max(0.0, (effective_quota - tokens_today) / TOKENS_PER_MESSAGE)
        
        created = u.get("created_at")
        created_str = created.strftime('%Y-%m-%d %H:%M') if isinstance(created, datetime) else "N/A"
        
        results.append({
            "user_id": uid_str,
            "name": u.get("name", "Unknown"),
            "email": u.get("email", ""),
            "role": u.get("role", "user"),
            "status": u.get("status", "active"),
            "custom_quota": custom_q,
            "quota": effective_quota,
            "tokens_today": tokens_today,
            "tokens_total": tokens_total,
            "messages_left": round(messages_left, 1),
            "created_at": created_str
        })
        
    return {"users": results}

@app.post("/api/admin/users/{user_id}/role")
async def admin_update_user_role(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    new_role = payload.get("role")
    if new_role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role specified")
        
    res = await db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"role": new_role}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
        
    return {"message": f"User role updated to {new_role}"}

@app.post("/api/admin/users/{user_id}/status")
async def admin_update_user_status(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    new_status = payload.get("status")
    if new_status not in ("active", "suspended"):
        raise HTTPException(status_code=400, detail="Invalid status specified")
        
    res = await db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"status": new_status}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
        
    return {"message": f"User status updated to {new_status}"}

@app.post("/api/admin/users/{user_id}/quota")
async def admin_update_user_quota(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    reset_today = payload.get("reset_today", False)
    custom_quota = payload.get("custom_quota")
    
    if reset_today:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        await db["usage_logs"].delete_many({"user_id": user_id, "date": today})
        
    if custom_quota is not None:
        try:
            custom_q_val = int(custom_quota) if custom_quota != "" else None
            if custom_q_val is None:
                await db["users"].update_one({"_id": ObjectId(user_id)}, {"$unset": {"custom_quota": ""}})
            else:
                await db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"custom_quota": custom_q_val}})
        except ValueError:
            raise HTTPException(status_code=400, detail="Quota must be a valid integer")
            
    return {"message": "User quota updated successfully"}

@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if current_user["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")
        
    res = await db["users"].delete_one({"_id": ObjectId(user_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
        
    await db["usage_logs"].delete_many({"user_id": user_id})
    await db["manuscripts"].delete_many({"user_id": user_id})
    await db["pdf_chats"].delete_many({"user_id": user_id})
    return {"message": "User and associated data deleted"}

@app.get("/api/admin/system-status")
async def admin_system_status(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    sources = []
    
    # 1. Groq API Check
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        sources.append({"name": "Groq LLM Engine", "type": "LLM Provider", "status": "no_key", "details": "GROQ_API_KEY missing"})
    else:
        try:
            start_t = time.time()
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {groq_key}"})
                latency = round((time.time() - start_t) * 1000)
                if res.status_code == 200:
                    sources.append({"name": "Groq LLM Engine", "type": "LLM Provider", "status": "operational", "latency_ms": latency, "details": "Models operational"})
                elif res.status_code == 429:
                    sources.append({"name": "Groq LLM Engine", "type": "LLM Provider", "status": "rate_limited", "latency_ms": latency, "details": "Rate limit active"})
                else:
                    sources.append({"name": "Groq LLM Engine", "type": "LLM Provider", "status": "degraded", "latency_ms": latency, "details": f"HTTP {res.status_code}"})
        except Exception as e:
            sources.append({"name": "Groq LLM Engine", "type": "LLM Provider", "status": "offline", "details": str(e)})

    # 2. Google Gemini Check
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        sources.append({"name": "Google Gemini Engine", "type": "LLM Provider", "status": "no_key", "details": "GEMINI_API_KEY missing"})
    else:
        try:
            start_t = time.time()
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}")
                latency = round((time.time() - start_t) * 1000)
                if res.status_code == 200:
                    sources.append({"name": "Google Gemini Engine", "type": "LLM Provider", "status": "operational", "latency_ms": latency, "details": "Operational"})
                else:
                    sources.append({"name": "Google Gemini Engine", "type": "LLM Provider", "status": "degraded", "latency_ms": latency, "details": f"HTTP {res.status_code}"})
        except Exception as e:
            sources.append({"name": "Google Gemini Engine", "type": "LLM Provider", "status": "offline", "details": str(e)})

    # 3. Semantic Scholar Search Check
    try:
        start_t = time.time()
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get("https://api.semanticscholar.org/graph/v1/paper/autocomplete?query=solar")
            latency = round((time.time() - start_t) * 1000)
            if res.status_code in (200, 400):
                sources.append({"name": "Semantic Scholar API", "type": "Literature Source", "status": "operational", "latency_ms": latency, "details": "Active & Searchable"})
            elif res.status_code == 429:
                sources.append({"name": "Semantic Scholar API", "type": "Literature Source", "status": "rate_limited", "latency_ms": latency, "details": "Rate limit active"})
            else:
                sources.append({"name": "Semantic Scholar API", "type": "Literature Source", "status": "degraded", "details": f"HTTP {res.status_code}"})
    except Exception as e:
        sources.append({"name": "Semantic Scholar API", "type": "Literature Source", "status": "offline", "details": str(e)})

    # 4. arXiv Search Check
    try:
        start_t = time.time()
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get("https://export.arxiv.org/api/query?search_query=all:electron&max_results=1")
            latency = round((time.time() - start_t) * 1000)
            if res.status_code == 200:
                sources.append({"name": "arXiv Search API", "type": "Literature Source", "status": "operational", "latency_ms": latency, "details": "Active"})
            else:
                sources.append({"name": "arXiv Search API", "type": "Literature Source", "status": "degraded", "details": f"HTTP {res.status_code}"})
    except Exception as e:
        sources.append({"name": "arXiv Search API", "type": "Literature Source", "status": "offline", "details": str(e)})

    # 5. PubMed NCBI Check
    try:
        start_t = time.time()
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pmc&term=cancer&retmode=json&retmax=1")
            latency = round((time.time() - start_t) * 1000)
            if res.status_code == 200:
                sources.append({"name": "PubMed NCBI Service", "type": "Literature Source", "status": "operational", "latency_ms": latency, "details": "Active"})
            else:
                sources.append({"name": "PubMed NCBI Service", "type": "Literature Source", "status": "degraded", "details": f"HTTP {res.status_code}"})
    except Exception as e:
        sources.append({"name": "PubMed NCBI Service", "type": "Literature Source", "status": "offline", "details": str(e)})

    # 6. GROBID PDF Engine Check
    try:
        start_t = time.time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            res = await client.get("http://localhost:8070/api/isalive")
            latency = round((time.time() - start_t) * 1000)
            if res.status_code == 200:
                sources.append({"name": "GROBID Structuring Engine", "type": "PDF Parser", "status": "operational", "latency_ms": latency, "details": "Local GROBID active"})
            else:
                sources.append({"name": "GROBID Structuring Engine", "type": "PDF Parser", "status": "degraded", "details": "Resuming heuristic fallback"})
    except Exception:
        sources.append({"name": "GROBID Structuring Engine", "type": "PDF Parser", "status": "offline", "details": "Unreachable (Using PyMuPDF Fallback)"})

    return {"sources": sources}

# Trigger reload