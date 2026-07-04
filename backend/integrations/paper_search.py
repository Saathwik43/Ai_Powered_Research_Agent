import asyncio
import math
import time
import logging
from integrations.openalex import search_papers as openalex_search
from integrations.arxiv import search_papers as arxiv_search
from integrations.semanticscholar import search_papers as s2_search
from integrations.crossref import search_works as crossref_search
from integrations.github_knowledge import search_github_knowledge
from integrations.pubmed import search_papers as pubmed_search

logger = logging.getLogger(__name__)
_cache = {}

_CURRENT_YEAR = 2026


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation for dedup comparison."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _deduplicate(papers: list) -> list:
    """Remove duplicate papers by normalized title similarity."""
    seen = set()
    unique = []
    for paper in papers:
        key = _normalize_title(paper.get("title", ""))[:60]
        if key and key not in seen:
            seen.add(key)
            unique.append(paper)
    return unique


# ─── Relevance-Based Scoring ──────────────────────────────────────────────────

_SOURCE_WEIGHTS = {
    "Semantic Scholar": 0.9,
    "OpenAlex": 0.7,
    "PubMed": 0.65,
    "Crossref": 0.6,
    "arXiv": 0.5,
}
# GitHub sub-sources all start with "GitHub/"
_GITHUB_SOURCE_WEIGHT = 0.3


def _compute_score(paper: dict) -> float:
    """
    Combined relevance score from three signals:
      - Citation count (log-scaled, 40% weight)
      - Source relevance weight (30% weight)
      - Recency bonus (30% weight — papers from last 3 years boosted)
    """
    # Citation signal — log-scaled, capped at 1.0
    citations = paper.get("citations", 0) or 0
    citation_score = min(math.log1p(citations) / 10.0, 1.0)

    # Source relevance weight
    source = paper.get("source", "")
    if source.startswith("GitHub"):
        source_score = _GITHUB_SOURCE_WEIGHT
    else:
        source_score = _SOURCE_WEIGHTS.get(source, 0.4)

    # Recency bonus — linear decay over 10 years
    year = paper.get("year", "")
    try:
        recency = max(0.0, 1.0 - (_CURRENT_YEAR - int(year)) / 10.0) if str(year).isdigit() else 0.3
    except (ValueError, TypeError):
        recency = 0.3

    return 0.4 * citation_score + 0.3 * source_score + 0.3 * recency


def _rank_papers(papers: list) -> list:
    """Sort papers by combined relevance score (descending)."""
    for p in papers:
        p["_relevance_rank"] = round(_compute_score(p), 4)
    papers.sort(key=lambda p: p["_relevance_rank"], reverse=True)
    return papers


async def search_all(query: str, limit: int = 15) -> list:
    """
    Run all 6 paper sources in parallel under a 6-second soft ceiling.

    Sources
    -------
    - Semantic Scholar (highest relevance score)
    - OpenAlex (citation-count sorted)
    - Crossref (works search)
    - PubMed (E-utilities esearch → esummary, two-call pattern)
    - arXiv (relevance / newest)
    - GitHub knowledge (awesome-repo links)

    Post-processing
    ----------------
    1. Tag sources that don't self-tag
    2. Merge all results
    3. Deduplicate by normalised title
    4. Rank by combined score (citations × source weight × recency)
    5. Enrich with Unpaywall open-access links (DOI-based)

    Timeout behaviour — asyncio.wait() with per-task cancellation
    -------------------------------------------------------------
    Tasks are created with asyncio.create_task() and submitted to
    asyncio.wait(tasks, timeout=6.0).  After 6 seconds:
      - `done`    → tasks that finished in time; results are harvested.
      - `pending` → tasks still running; each is cancelled individually.

    This gives **partial results**: fast sources (S2, OpenAlex, arXiv)
    that complete within 6s are always returned, even if a slow source
    (PubMed, CrossRef) hasn't finished.  The old asyncio.wait_for(gather())
    pattern cancelled every task the moment *any one* source hit the ceiling,
    which caused broad queries like "machine learning" to return an empty list
    whenever PubMed was slow.

    CrossRef slowdown lessons applied
    ----------------------------------
    - No asyncio.sleep() anywhere in the call chain.
    - Each source uses its own per-call timeout (5s).
    - PubMed aborts after esearch if that call alone takes >3s.
    - Exceptions from individual tasks are caught per-task, not via
      return_exceptions=True on gather.
    """
    cache_key = f"{query}_{limit}"
    now = time.time()
    if cache_key in _cache:
        cached_data, timestamp = _cache[cache_key]
        if now - timestamp < 600:  # 10 minutes TTL
            logger.info(f"Returning cached literature results for {query}")
            return cached_data

    # Create named tasks so we can associate results back to their source.
    named = [
        ("SemanticScholar", asyncio.create_task(s2_search(query, limit=limit),       name="SemanticScholar")),
        ("OpenAlex",        asyncio.create_task(openalex_search(query, limit=limit),  name="OpenAlex")),
        ("Crossref",        asyncio.create_task(crossref_search(query, limit=limit),  name="Crossref")),
        ("PubMed",          asyncio.create_task(pubmed_search(query, limit=limit),    name="PubMed")),
        ("arXiv",           asyncio.create_task(arxiv_search(query, limit=limit),     name="arXiv")),
        ("GitHub",          asyncio.create_task(
                                asyncio.to_thread(search_github_knowledge, query),    name="GitHub")),
    ]
    task_to_name = {task: name for name, task in named}
    all_tasks = {task for _, task in named}

    done, pending = await asyncio.wait(all_tasks, timeout=6.0)

    # Cancel only the stragglers — tasks that already finished are untouched.
    if pending:
        slow_names = [task_to_name[t] for t in pending]
        logger.warning(
            f"search_all() 6s ceiling: cancelling {len(pending)} slow source(s): "
            f"{slow_names}.  Returning partial results from {len(done)} fast source(s)."
        )
        for task in pending:
            task.cancel()
        # Drain cancellations so no dangling coroutines remain.
        await asyncio.gather(*pending, return_exceptions=True)

    # If every source timed out (done is empty), fall back to stale cache or [].
    if not done:
        logger.warning("search_all(): all sources timed out, returning stale cache or [].")
        cached = _cache.get(cache_key)
        return cached[0] if cached else []

    # Harvest results from the completed tasks; catch per-task exceptions.
    results_map: dict[str, list] = {name: [] for name, _ in named}
    for task in done:
        name = task_to_name[task]
        exc = task.exception()
        if exc is not None:
            logger.error(f"Search task failed ({name}): {exc}")
        else:
            results_map[name] = task.result() or []

    s2_results       = results_map["SemanticScholar"]
    openalex_results = results_map["OpenAlex"]
    crossref_results = results_map["Crossref"]
    pubmed_results   = results_map["PubMed"]
    arxiv_results    = results_map["arXiv"]
    github_results   = results_map["GitHub"]

    # Tag sources that don't already have one
    for p in openalex_results:
        p.setdefault("source", "OpenAlex")
    for p in arxiv_results:
        p.setdefault("source", "arXiv")
    for p in github_results:
        p.setdefault("source", p.get("source", "GitHub"))
    for p in crossref_results:
        p.setdefault("source", "Crossref")
    for p in pubmed_results:
        p.setdefault("source", "PubMed")
    # Semantic Scholar already tags its own in semanticscholar.py

    # Merge all sources into a single list
    merged = (
        s2_results
        + openalex_results
        + crossref_results
        + pubmed_results
        + arxiv_results
        + github_results
    )

    # Deduplicate
    unique = _deduplicate(merged)

    # Rank by combined relevance score instead of source order
    unique = _rank_papers(unique)

    # Enrich with Unpaywall open-access links (non-blocking best-effort)
    try:
        from integrations.unpaywall import enrich_papers_with_oa
        unique = await enrich_papers_with_oa(unique)
    except Exception as e:
        logger.warning(f"Unpaywall enrichment failed (non-fatal): {e}")

    _cache[cache_key] = (unique, now)
    return unique
