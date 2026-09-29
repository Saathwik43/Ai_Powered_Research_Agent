"""Finding prior work: topic discovery, unified literature search, arXiv,
Crossref, the GitHub knowledge base, and saved literature surveys."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

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
)
from integrations.paper_search import (
    SHARED_LIMIT_PER_SOURCE,
    search_all,
    search_all_with_meta,
)
from integrations.snowball import DEFAULT_LIMIT as SNOWBALL_DEFAULT_LIMIT, snowball
from ai.paper_brief import (
    INLINE_BRIEF_COUNT,
    WARM_BRIEF_COUNT,
    brief_papers,
    briefs_within,
    deep_brief_paper,
    warm_briefs,
)
from schemas import (
    LiteratureBriefPayload,
    LiteratureDeepBriefPayload,
    LiteratureSavePayload,
    LiteratureSnowballPayload,
)
from services.query_history import (
    _suggest_rank,
    normalize_suggest_input,
    recent_queries_shared,
    record_query_shared,
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

# A citation expansion is capped well below a search: every row has to be worth
# reading on its provenance alone, and the tail of a snowball is papers a single
# seed happened to cite once.
SNOWBALL_MAX_LIMIT = 60

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


async def _attach_briefs(papers: list, background: BackgroundTasks) -> list:
    """Card briefings for the first page, without making search wait on them.

    The head of the page is briefed inline under a deadline, so those cards
    arrive complete instead of showing the extractive split and then swapping
    under the reader a second later. The tail of the same page is warmed into
    the shared cache after the response is sent, so scrolling hits it warm.
    Papers beyond the first page are briefed on demand, as before.

    Papers are copied rather than mutated: these dicts are the cached search
    results, and a `brief` written into them would be served as part of the
    next cache hit for a different query.
    """
    if not papers:
        return papers
    head = papers[:INLINE_BRIEF_COUNT]
    briefed = list(papers)
    for index, bullets in enumerate(await briefs_within(head)):
        if bullets:
            briefed[index] = {**briefed[index], "brief": bullets}
    tail = papers[INLINE_BRIEF_COUNT:WARM_BRIEF_COUNT]
    if tail:
        background.add_task(warm_briefs, tail)
    return briefed


@router.get("/api/literature")
@limiter.limit("5/minute")
async def get_literature(
    request: Request,
    background: BackgroundTasks,
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
    briefed = await _attach_briefs(filtered, background)
    response = {
        "data": briefed,
        "count": len(filtered),
        "total": total,
        # Unclassified papers remain, so another page is genuinely available.
        "has_more": examined < total,
        "limit": effective_limit,
    }
    if filtered:
        # Only successful queries become suggestions — proposing a query that
        # finds nothing is worse than proposing nothing.
        await record_query_shared(query)

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
# Fires on every keystroke behind a 250ms debounce, so it needs headroom the
# search routes do not.
@limiter.limit("120/minute")
async def suggest_queries(request: Request, q: str = "", current_user: dict = Depends(get_current_user)):
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
    pool = await recent_queries_shared() + _SEED_SUGGESTIONS

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
@limiter.limit("20/minute")
async def arxiv_search_endpoint(request: Request, query: str, limit: int = 10, current_user: dict = Depends(get_current_user)):
    """Search arXiv directly by keyword."""
    if not validate_input_layers_a_b(query):
        return {"data": [], "count": 0, "coherence_check": "failed"}
    from integrations.arxiv import search_papers as arxiv_search
    papers = await arxiv_search(query, limit=limit)
    return {"data": papers, "count": len(papers)}


# ─── arXiv — Category RSS Feed ────────────────────────────────────────────────

@router.get("/api/arxiv/feed")
@limiter.limit("30/minute")
async def arxiv_feed(request: Request, category: str = "cs.AI", limit: int = 10, current_user: dict = Depends(get_current_user)):
    """
    Fetch latest papers from an arXiv RSS category feed.
    category: arXiv code e.g. cs.AI, cs.LG, cs.CR, cs.CV, cs.CL, quant-ph, q-bio.GN
    """
    papers = await fetch_category_feed(category, limit=limit)
    return {"data": papers, "category": category, "count": len(papers)}


@router.get("/api/arxiv/trending")
# Six category feeds per call.
@limiter.limit("15/minute")
async def arxiv_trending(request: Request, current_user: dict = Depends(get_current_user)):
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
@limiter.limit("20/minute")
async def get_crossref_journals(request: Request, query: str, current_user: dict = Depends(get_current_user)):
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

# The knowledge base is read-only at runtime. Cloning moved to
# `scripts/sync_github_repos.py`, which the deploy runs before the app boots —
# a `git clone` triggered by a request holds a worker for minutes and writes
# hundreds of megabytes of container disk on demand (audit API-GH). There is no
# POST /api/github/sync any more.

@router.get("/api/github/repos")
@limiter.limit("30/minute")
async def get_github_repos(request: Request, current_user: dict = Depends(get_current_user)):
    """List all configured GitHub knowledge repos and whether they are on disk."""
    return {"data": list_all_repos()}


@router.get("/api/github/categories")
@limiter.limit("30/minute")
async def get_github_categories(request: Request, repo: str = "papers-we-love", current_user: dict = Depends(get_current_user)):
    """List categories in a specific GitHub repo."""
    cats = await asyncio.to_thread(list_categories, repo)
    if not cats:
        return {
            "data": [],
            "message": (
                f"Repo '{repo}' is not present in this deployment. It is checked out at "
                "build time by `python -m scripts.sync_github_repos`."
            ),
        }
    return {"data": cats}


@router.get("/api/github/papers")
@limiter.limit("30/minute")
async def get_github_papers(request: Request, repo: str = "papers-we-love", category: str = "", current_user: dict = Depends(get_current_user)):
    """List papers in a category of a GitHub repo."""
    papers = await asyncio.to_thread(find_papers_by_category, category, repo)
    return {"data": papers, "count": len(papers)}


@router.get("/api/github/search")
# Each call walks every markdown file in every checked-out repo, so it is disk
# work proportional to the corpus, not a lookup.
@limiter.limit("20/minute")
async def search_github(request: Request, query: str, current_user: dict = Depends(get_current_user)):
    """Search all checked-out GitHub repos for papers matching the query."""
    if not validate_input_layers_a_b(query):
        return {"data": [], "count": 0, "coherence_check": "failed"}
    results = await asyncio.to_thread(search_github_knowledge, query)
    return {"data": results, "count": len(results)}


# ─── Save / Load Literature Survey (per user) ─────────────────────────────────

@router.post("/api/literature/brief")
@limiter.limit("20/minute")
async def brief_literature(
    request: Request,
    payload: LiteratureBriefPayload,
    current_user: dict = Depends(get_current_user),
):
    """Plain-language 4–5 bullets per paper, grounded in title + abstract only."""
    papers = [p.model_dump() for p in (payload.papers or [])]
    bullets_list = await brief_papers(papers)
    return {"briefs": [{"bullets": bullets} for bullets in bullets_list]}


@router.post("/api/literature/deep-brief")
# One paper per call, and a call can fetch and parse a PDF — an order of
# magnitude more expensive than the abstract briefing above.
@limiter.limit("10/minute")
async def deep_brief_literature(
    request: Request,
    payload: LiteratureDeepBriefPayload,
    current_user: dict = Depends(get_current_user),
):
    """Briefing read from the paper's full text, for a card the reader opened.

    Cheapest structured source first (arXiv HTML, arXiv LaTeX, Europe PMC JATS)
    and the OA PDF only if none of those answer. Falls back to the abstract
    briefing, reporting `source: "abstract"`, when there is no readable full
    text — which is most closed-access papers.
    """
    return await deep_brief_paper(payload.paper.model_dump())


@router.post("/api/literature/snowball")
# A snowball is up to MAX_SEEDS x 2 graphs x 2 directions citation requests, so
# it is rate-limited like /api/literature rather than like the brief endpoints.
@limiter.limit("5/minute")
async def snowball_literature(
    request: Request,
    payload: LiteratureSnowballPayload,
    current_user: dict = Depends(get_current_user),
):
    """Papers reached from a seed set by following citations both ways (2.2).

    Backward is each seed's reference list; forward is the works citing it. Both
    graphs (OpenAlex, Semantic Scholar) are traversed and merged — they disagree
    about a large share of any paper's edges.

    Unlike /api/literature this does **not** run the relevance classifier. A
    paper four of the user's own seeds cite is relevant by construction, and its
    abstract often does not mention the query terms at all — the classifier
    would throw away exactly the prior work snowballing exists to find. The
    provenance on each row (`snowball_seeds`) is what justifies it instead.
    """
    if payload.direction not in ("backward", "forward", "both"):
        raise HTTPException(
            status_code=422,
            detail="direction must be 'backward', 'forward' or 'both'.",
        )

    seeds = [s.model_dump() for s in (payload.seeds or [])]
    if not seeds:
        raise HTTPException(status_code=422, detail="At least one seed paper is required.")

    limit = max(1, min(payload.limit or SNOWBALL_DEFAULT_LIMIT, SNOWBALL_MAX_LIMIT))
    papers, meta = await snowball(
        seeds,
        direction=payload.direction,
        query=payload.query or "",
        limit=limit,
    )
    return {
        "data": papers,
        "count": len(papers),
        "limit": limit,
        **meta.as_dict(),
    }


@router.post("/api/literature/save")
@limiter.limit("30/minute")
async def save_literature(request: Request, payload: LiteratureSavePayload, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    now = datetime.now(timezone.utc).isoformat()
    screened = payload.screened if payload.screened is not None else len(payload.papers or [])
    fields = {
        "papers": payload.papers,
        "saved_at": now,
        "screened": screened,
        "sources": payload.sources or [],
    }
    existing = await collection.find_one({"user_id": user_id, "query": payload.query})
    if existing:
        await collection.update_one({"user_id": user_id, "query": payload.query}, {"$set": fields})
        return {"message": "Literature survey updated.", "query": payload.query}
    else:
        await collection.insert_one({"user_id": user_id, "query": payload.query, **fields})
        return {"message": "Literature survey saved.", "query": payload.query}


@router.get("/api/literature/load")
@limiter.limit("60/minute")
async def load_literature(request: Request, query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    doc = await collection.find_one({"user_id": user_id, "query": query}, {"_id": 0, "user_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="No saved survey found for this query.")
    return {"data": doc}


@router.get("/api/literature/list")
@limiter.limit("60/minute")
async def list_literature_surveys(
    request: Request,
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
):
    user_id = current_user["user_id"]
    collection = db["literature"]
    query = {"user_id": user_id}
    if cursor:
        try:
            query["_id"] = {"$lt": ObjectId(cursor)}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor.")
    docs = await (
        collection.find(query, {"user_id": 0})
        .sort("_id", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    next_cursor = str(docs[-1]["_id"]) if len(docs) == limit else None
    surveys = []
    for doc in docs:
        doc.pop("_id", None)
        surveys.append(doc)
    return {"data": surveys, "next_cursor": next_cursor}

@router.delete("/api/literature/delete/{query}")
@limiter.limit("30/minute")
async def delete_literature(request: Request, query: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    collection = db["literature"]
    result = await collection.delete_one({"user_id": user_id, "query": query})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Survey not found.")
    return {"message": "Literature survey deleted successfully."}
