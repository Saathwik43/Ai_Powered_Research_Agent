from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime, timezone

from ai.topic_discovery import discover_topics
from ai.manuscript_generation import generate_section
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
from database import db, ping_db
from auth import signup_user, login_user, get_current_user, seed_admin

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ping_db()
    await seed_admin()
    yield


app = FastAPI(title="AI-Powered Research Paper Publishing Agent", lifespan=lifespan)

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


# ─── Root ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "Welcome to the Research Agent API"}


# ─── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/signup")
async def signup(payload: dict):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    name = payload.get("name", "").strip()
    if not email or not password or not name:
        raise HTTPException(status_code=400, detail="Name, email, and password are required.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    return await signup_user(email, password, name)


@app.post("/api/auth/login")
async def login(payload: dict):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    return await login_user(email, password)


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


@app.post("/api/github/sync")
async def sync_github(payload: dict = {}, current_user: dict = Depends(get_current_user)):
    """
    Sync one or all GitHub repos.
    payload: {"repo": "awesome-datascience"} or {} to sync all.
    """
    import asyncio
    repo_name = payload.get("repo")
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
async def create_manuscript_section(payload: dict, current_user: dict = Depends(get_current_user)):
    topic = payload.get("topic", "")
    section = payload.get("section", "abstract")
    context = payload.get("context", "")
    content = await generate_section(topic, section, context)
    return {"section": section, "content": content}


@app.post("/api/manuscript/save")
async def save_manuscript_draft(payload: dict, current_user: dict = Depends(get_current_user)):
    topic = payload.get("topic", "")
    content = payload.get("content", {})
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required.")

    user_id = current_user["user_id"]
    collection = db["manuscripts"]
    now = datetime.now(timezone.utc).isoformat()
    existing = await collection.find_one({"user_id": user_id, "topic": topic})
    if existing:
        await collection.update_one(
            {"user_id": user_id, "topic": topic},
            {"$set": {"content": content, "updated_at": now}}
        )
        return {"message": "Draft updated.", "topic": topic}
    else:
        await collection.insert_one({
            "user_id": user_id,
            "topic": topic,
            "content": content,
            "created_at": now,
            "updated_at": now,
        })
        return {"message": "Draft saved.", "topic": topic}


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
async def get_venues(payload: dict, current_user: dict = Depends(get_current_user)):
    abstract = payload.get("abstract", "")
    domain = payload.get("domain", "")
    venues = await recommend_venues(abstract, domain)
    return {"data": venues}


# ─── Guideline Alignment ───────────────────────────────────────────────────────

@app.post("/api/guidelines")
async def get_guidelines(payload: dict, current_user: dict = Depends(get_current_user)):
    manuscript = payload.get("manuscript", {})
    venue = payload.get("venue", {})
    if not venue.get("name"):
        raise HTTPException(status_code=400, detail="Venue name is required.")
    result = await align_guidelines(manuscript, venue)
    return {"data": result}


# ─── Save / Load Literature Survey (per user) ─────────────────────────────────

@app.post("/api/literature/save")
async def save_literature(payload: dict, current_user: dict = Depends(get_current_user)):
    query = payload.get("query", "")
    papers = payload.get("papers", [])
    if not query:
        raise HTTPException(status_code=400, detail="Query is required.")

    user_id = current_user["user_id"]
    collection = db["literature"]
    existing = await collection.find_one({"user_id": user_id, "query": query})
    if existing:
        await collection.update_one({"user_id": user_id, "query": query}, {"$set": {"papers": papers}})
        return {"message": "Literature survey updated.", "query": query}
    else:
        await collection.insert_one({"user_id": user_id, "query": query, "papers": papers})
        return {"message": "Literature survey saved.", "query": query}


@app.get("/api/literature/load")
async def load_literature(query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    doc = await collection.find_one({"user_id": user_id, "query": query}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No saved survey found for this query.")
    return {"data": doc}
