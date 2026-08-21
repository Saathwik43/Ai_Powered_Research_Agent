"""Finding prior work: topic discovery, unified literature search, arXiv,
Crossref, the GitHub knowledge base, and saved literature surveys."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ai.guardrails import validate_input_layers_a_b
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
from integrations.paper_search import (
    SHARED_LIMIT_PER_SOURCE,
    search_all,
    search_all_with_meta,
)
from schemas import GithubSyncPayload, LiteratureSavePayload
from services.query_history import (
    _suggest_rank,
    normalize_suggest_input,
    recent_queries,
    record_query,
)

router = APIRouter(tags=["discovery"])


# ─── Topic Discovery ───────────────────────────────────────────────────────────

@router.get("/api/topics")
@limiter.limit("5/minute")
async def get_topics(request: Request, intent: str, current_user: dict = Depends(get_current_user)):
    """
    Rate-limited to match /api/literature. The Dashboard fires both for one
    query and they trigger the same 11-source fan-out; throttling only one of
    them let the other keep burning source quota after the limit tripped.
    """
    result = await discover_topics(intent)
    return result


# ─── Literature — Unified Search (OpenAlex + arXiv + GitHub) ──────────────────

LITERATURE_DEFAULT_LIMIT = 50
LITERATURE_MAX_LIMIT = 100

# Extra papers classified per round beyond what is still needed, so a round
# that drops several irrelevant papers usually still fills the request without
# a second round. Higher wastes LLM calls; lower needs more round-trips.
_BACKFILL_HEADROOM = 10


async def _collect_relevant(query: str, papers: list, wanted: int) -> tuple[list, int]:
    """
    Classify *papers* in ranked order until *wanted* relevant ones are found.

    Returns ``(relevant, examined)``. Slicing to *wanted* before filtering — as
    this endpoint used to do — meant a request for 15 papers could return 6,
    with no attempt to backfill from the ranked papers sitting right behind the
    window. Classifying in rounds keeps the LLM cost proportional to what is
    actually returned while still filling the request.
    """
    relevant: list = []
    examined = 0

    while examined < len(papers) and len(relevant) < wanted:
        need = wanted - len(relevant)
        batch = papers[examined:examined + need + _BACKFILL_HEADROOM]
        if not batch:
            break
        examined += len(batch)
        relevant.extend(await _filter_relevant_papers(query, batch))

    return relevant[:wanted], examined


@router.get("/api/literature")
@limiter.limit("5/minute")
async def get_literature(
    request: Request,
    query: str,
    limit: int = LITERATURE_DEFAULT_LIMIT,
    fresh: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """
    Unified literature search across every configured source.

    Applies the shared relevance filter used by manuscript generation so noisy
    cross-domain results do not leak into the literature view, backfilling from
    the ranked tail so *limit* means what it says.

    fresh=true bypasses the semantic cache — what the UI's "search instead for
    X" link sends when the user rejects a semantically matched result.
    """
    if not validate_input_layers_a_b(query):
        # Mirrors /api/topics: the same guardrail, so a query the Dashboard
        # rejects on one request cannot fan out to 11 sources on the other.
        return {
            "data": [], "count": 0, "total": 0,
            "has_more": False, "limit": 0, "coherence_check": "failed",
        }

    effective_limit = max(1, min(limit, LITERATURE_MAX_LIMIT))

    papers, meta = await search_all_with_meta(
        query,
        # Deliberately NOT scaled down for small limits. /api/topics fires in
        # parallel for the same query with this exact value, and the shared
        # cache entry is keyed on it — a Dashboard limit=6 asking for fewer
        # per source would split them into two separate fan-outs, which costs
        # far more than the papers it saves fetching.
        limit_per_source=SHARED_LIMIT_PER_SOURCE,
        allow_semantic_cache=not fresh,
    )
    total = len(papers)
    filtered, examined = await _collect_relevant(query, papers, effective_limit)
    response = {
        "data": filtered,
        "count": len(filtered),
        "total": total,
        # Unclassified papers remain, so another page is genuinely available.
        "has_more": examined < total,
        "limit": effective_limit,
    }
    if filtered:
        # Only successful queries become suggestions — proposing a query that
        # finds nothing is worse than proposing nothing.
        record_query(query)

    if meta.matched_query:
        # The user asked one question and is being shown another question's
        # results. Say so — the UI renders this as "Showing results for X,
        # search instead for Y".
        response["matched_query"] = meta.matched_query
        response["cache"] = meta.cache
    if meta.sources:
        response["sources"] = meta.sources_as_dicts()
    return response


# ─── Query Suggestions ─────────────────────────────────────────────────────────

# Seed list: what a first-time user sees before anyone has searched anything.
# Real suggestions come from queries this deployment has actually run.
_SEED_SUGGESTIONS = [
    "machine learning in healthcare", "deep learning for NLP", "computer vision",
    "cybersecurity threat detection", "quantum computing", "federated learning",
    "large language models", "autonomous vehicles", "reinforcement learning",
    "explainable AI", "edge computing", "generative AI", "drug discovery AI",
    "natural language processing", "neural architecture search", "robotics",
    "graph neural networks", "transformer attention mechanisms",
    "protein structure prediction", "climate modelling",
]

SUGGEST_LIMIT = 8


@router.get("/api/suggest")
async def suggest_queries(q: str = "", current_user: dict = Depends(get_current_user)):
    """
    Autocomplete for the search box.

    Ranked so acronyms work, which plain substring matching could not do:
    "CNN" matched nothing in the old hardcoded list because no entry contained
    that literal substring.

      1. prefix match on the whole phrase   ("mac" → "machine learning …")
      2. prefix match on any word           ("learn" → "machine learning …")
      3. initials match                     ("nas"  → "neural architecture search")
      4. substring anywhere                 (fallback)
    """
    prefix = normalize_suggest_input(q)
    pool = recent_queries() + _SEED_SUGGESTIONS

    seen: set[str] = set()
    candidates: list[str] = []
    for phrase in pool:
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            candidates.append(phrase)

    if not prefix:
        return {"data": candidates[:SUGGEST_LIMIT]}

    scored = []
    for phrase in candidates:
        rank = _suggest_rank(prefix, phrase)
        if rank is not None:
            scored.append((rank, len(phrase), phrase))

    scored.sort()
    return {"data": [phrase for _rank, _length, phrase in scored[:SUGGEST_LIMIT]]}


# ─── arXiv — Keyword Search ────────────────────────────────────────────────────

@router.get("/api/arxiv/search")
async def arxiv_search_endpoint(query: str, limit: int = 10, current_user: dict = Depends(get_current_user)):
    """Search arXiv directly by keyword."""
    if not validate_input_layers_a_b(query):
        return {"data": [], "count": 0, "coherence_check": "failed"}
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
    if not validate_input_layers_a_b(query):
        return {"data": [], "count": 0, "coherence_check": "failed"}
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
