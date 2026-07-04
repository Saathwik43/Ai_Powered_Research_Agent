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
    Run all 6 paper sources in parallel under a strict 6-second ceiling.

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

    Timeout behaviour
    -----------------
    The entire gather() is wrapped in asyncio.wait_for(timeout=6.0).
    If any source (or combination) keeps all 6 tasks from completing within
    6 seconds, asyncio.wait_for cancels every in-flight task and we return
    whatever is in the search cache for this query, or an empty list.
    This guarantees the endpoint never hangs past ~6s regardless of which
    source is misbehaving — PubMed's two-call pattern is the highest-risk
    candidate and the reason the ceiling is enforced here.

    CrossRef slowdown lessons applied
    ----------------------------------
    - No asyncio.sleep() anywhere in the call chain.
    - Each source uses its own per-call timeout (5s).
    - PubMed aborts after esearch if that call alone takes >3s.
    - return_exceptions=True so a single failing source never raises.
    """
    cache_key = f"{query}_{limit}"
    now = time.time()
    if cache_key in _cache:
        cached_data, timestamp = _cache[cache_key]
        if now - timestamp < 600:  # 10 minutes TTL
            logger.info(f"Returning cached literature results for {query}")
            return cached_data

    coros = [
        s2_search(query, limit=limit),
        openalex_search(query, limit=limit),
        crossref_search(query, limit=limit),
        pubmed_search(query, limit=limit),
        arxiv_search(query, limit=limit),
        asyncio.to_thread(search_github_knowledge, query),
    ]
    source_names = ["SemanticScholar", "OpenAlex", "Crossref", "PubMed", "arXiv", "GitHub"]

    try:
        all_results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=6.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "search_all() exceeded 6s ceiling — all sources cancelled. "
            "Returning cached results if available, otherwise empty list."
        )
        cached = _cache.get(cache_key)
        return cached[0] if cached else []

    # Unpack results; replace exceptions with empty lists and log them.
    results_map: dict[str, list] = {}
    for name, result in zip(source_names, all_results):
        if isinstance(result, Exception):
            logger.error(f"Search task failed ({name}): {result}")
            results_map[name] = []
        else:
            results_map[name] = result or []

    s2_results        = results_map["SemanticScholar"]
    openalex_results  = results_map["OpenAlex"]
    crossref_results  = results_map["Crossref"]
    pubmed_results    = results_map["PubMed"]
    arxiv_results     = results_map["arXiv"]
    github_results    = results_map["GitHub"]

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

