"""Finding prior work: topic discovery, unified literature search, arXiv,
Crossref, the GitHub knowledge base, and saved literature surveys."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ai.relevance import _filter_relevant_papers
from ai.topic_discovery import discover_topics
from core.auth import get_current_user
from core.limiter import limiter
from core.database import db
from integrations.arxiv import fetch_category_feed
from integrations.crossref import search_journals
from integrations.github_knowledge import (
    find_papers_by_category,
    list_all_repos,
    list_categories,
    search_github_knowledge,
    sync_all_repositories,
    sync_repository,
)
from integrations.paper_search import SHARED_LIMIT_PER_SOURCE, search_all
from schemas import GithubSyncPayload, LiteratureSavePayload

router = APIRouter(tags=["discovery"])


# ─── Topic Discovery ───────────────────────────────────────────────────────────

@router.get("/api/topics")
async def get_topics(intent: str, current_user: dict = Depends(get_current_user)):
    result = await discover_topics(intent)
    return result


# ─── Literature — Unified Search (OpenAlex + arXiv + GitHub) ──────────────────

LITERATURE_DEFAULT_LIMIT = 50
LITERATURE_MAX_LIMIT = 100


@router.get("/api/literature")
@limiter.limit("5/minute")
async def get_literature(
    request: Request,
    query: str,
    limit: int = LITERATURE_DEFAULT_LIMIT,
    current_user: dict = Depends(get_current_user),
):
    """
    Unified literature search across OpenAlex, arXiv, and GitHub knowledge bases.
    Applies the shared relevance filter used by manuscript generation so noisy
    cross-domain results do not leak into the literature view.

    search_all() returns results already ranked, so the response window is cut
    to *limit* before relevance classification: the classifier costs one LLM
    call per paper, and papers past the window are never returned.
    """
    effective_limit = max(1, min(limit, LITERATURE_MAX_LIMIT))

    papers = await search_all(query, limit_per_source=SHARED_LIMIT_PER_SOURCE)
    total = len(papers)
    window = papers[:effective_limit]
    filtered = await _filter_relevant_papers(query, window)
    return {
        "data": filtered,
        "count": len(filtered),
        "total": total,
        "has_more": total > len(window),
        "limit": effective_limit,
    }


# ─── arXiv — Keyword Search ────────────────────────────────────────────────────

@router.get("/api/arxiv/search")
async def arxiv_search_endpoint(query: str, limit: int = 10, current_user: dict = Depends(get_current_user)):
    """Search arXiv directly by keyword."""
    from integrations.arxiv import search_papers as arxiv_search
    papers = await arxiv_search(query, limit=limit)
    return {"data": papers, "count": len(papers)}


# ─── arXiv — Category RSS Feed ────────────────────────────────────────────────

@router.get("/api/arxiv/feed")
async def arxiv_feed(category: str = "cs.AI", limit: int = 10, current_user: dict = Depends(get_current_user)):
    """
    Fetch latest papers from an arXiv RSS category feed.
    category: arXiv code e.g. cs.AI, cs.LG, cs.CR, cs.CV, cs.CL, quant-ph, q-bio.GN
    """
    papers = await fetch_category_feed(category, limit=limit)
    return {"data": papers, "category": category, "count": len(papers)}


@router.get("/api/arxiv/trending")
async def arxiv_trending(current_user: dict = Depends(get_current_user)):
    """
    Fetch latest papers from multiple arXiv categories at once for the dashboard.
    Returns a dict keyed by category code.
    """
    categories = ["cs.AI", "cs.LG", "cs.CR", "cs.CV", "cs.CL", "quant-ph"]
    feeds = await asyncio.gather(*[fetch_category_feed(c, limit=5) for c in categories])
    result = {}
    for cat, papers in zip(categories, feeds):
        result[cat] = papers
    return {"data": result}


# ─── Crossref Journal Search ───────────────────────────────────────────────────

@router.get("/api/crossref-journals")
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

@router.get("/api/github/repos")
async def get_github_repos(current_user: dict = Depends(get_current_user)):
    """List all configured GitHub knowledge repos and their sync status."""
    return {"data": list_all_repos()}


_sync_lock = asyncio.Lock()

@router.post("/api/github/sync")
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


@router.get("/api/github/categories")
async def get_github_categories(repo: str = "papers-we-love", current_user: dict = Depends(get_current_user)):
    """List categories in a specific GitHub repo."""
    cats = await asyncio.to_thread(list_categories, repo)
    if not cats:
        return {"data": [], "message": f"Repo '{repo}' not synced yet. POST /api/github/sync first."}
    return {"data": cats}


@router.get("/api/github/papers")
async def get_github_papers(repo: str = "papers-we-love", category: str = "", current_user: dict = Depends(get_current_user)):
    """List papers in a category of a GitHub repo."""
    papers = await asyncio.to_thread(find_papers_by_category, category, repo)
    return {"data": papers, "count": len(papers)}


@router.get("/api/github/search")
async def search_github(query: str, current_user: dict = Depends(get_current_user)):
    """Search all synced GitHub repos for papers matching the query."""
    results = await asyncio.to_thread(search_github_knowledge, query)
    return {"data": results, "count": len(results)}


# ─── Save / Load Literature Survey (per user) ─────────────────────────────────

@router.post("/api/literature/save")
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


@router.get("/api/literature/load")
async def load_literature(query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    doc = await collection.find_one({"user_id": user_id, "query": query}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No saved survey found for this query.")
    return {"data": doc}


@router.get("/api/literature/list")
async def list_literature_surveys(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    cursor = collection.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).sort("_id", -1)
    surveys = [doc async for doc in cursor]
    return {"data": surveys}

@router.delete("/api/literature/delete/{query}")
async def delete_literature(query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    result = await collection.delete_one({"user_id": user_id, "query": query})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Survey not found.")
    return {"message": "Literature survey deleted successfully."}
