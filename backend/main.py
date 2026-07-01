from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime, timezone
import logging
import asyncio
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from fastapi.responses import JSONResponse
import traceback

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from auth import decode_access_token
from fastapi import Request

from ai.topic_discovery import discover_topics
from ai.manuscript_generation import generate_section, edit_section
from ai.venue_recommendation import recommend_venues
from ai.guideline_alignment import align_guidelines
from integrations.paper_search import search_all
from integrations.arxiv import fetch_category_feed, fetch_multiple_feeds, CATEGORY_MAP
from integrations.crossref import search_journals
from integrations.github_knowledge import (
    sync_repository, sync_all_repositories,
    list_categories, list_all_repos,
    find_papers_by_category, search_github_knowledge
)

from database import db, ping_db, ensure_indexes
from auth import signup_user, login_user, get_current_user, seed_admin

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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

class ManuscriptPayload(BaseModel):
    topic: str
    section: str = "abstract"
    context: str = ""

class ManuscriptEditPayload(BaseModel):
    topic: str
    section: str = "abstract"
    current_content: str
    instructions: str

class ManuscriptSavePayload(BaseModel):
    topic: str
    content: Dict[str, Any]

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
async def signup(payload: SignupPayload):
    email = payload.email.strip().lower()
    return await signup_user(email, payload.password, payload.name.strip())

@app.post("/api/auth/login")
async def login(payload: LoginPayload):
    email = payload.email.strip().lower()
    return await login_user(email, payload.password)


@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


# ─── Topic Discovery ───────────────────────────────────────────────────────────

@app.get("/api/topics")
async def get_topics(intent: str, current_user: dict = Depends(get_current_user)):
    topics = await discover_topics(intent)
    return {"data": topics}


# ─── Literature — Unified Search (OpenAlex + arXiv + GitHub) ──────────────────

@app.get("/api/literature")
async def get_literature(query: str, limit: int = 8, current_user: dict = Depends(get_current_user)):
    """
    Unified literature search across OpenAlex, arXiv, and GitHub knowledge bases.
    Results are deduplicated and tagged by source.
    """
    papers = await search_all(query, limit=limit)
    return {"data": papers, "count": len(papers)}


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
async def create_manuscript_section(request: Request, payload: ManuscriptPayload, current_user: dict = Depends(get_current_user)):
    content = await generate_section(payload.topic, payload.section, payload.context)
    return {"section": payload.section, "content": content}

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
            {"$set": {"content": payload.content, "updated_at": now}}
        )
        return {"message": "Draft updated.", "topic": payload.topic}
    else:
        await collection.insert_one({
            "user_id": user_id,
            "topic": payload.topic,
            "content": payload.content,
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


# ─── Venue Recommendations ─────────────────────────────────────────────────────

@app.post("/api/venues")
async def get_venues(payload: VenuePayload, current_user: dict = Depends(get_current_user)):
    venues = await recommend_venues(payload.abstract, payload.domain)
    return {"data": venues}


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
