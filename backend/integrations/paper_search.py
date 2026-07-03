import asyncio
import time
import logging
from integrations.openalex import search_papers as openalex_search
from integrations.arxiv import search_papers as arxiv_search
from integrations.semanticscholar import search_papers as s2_search
from integrations.crossref import search_works as crossref_search
from integrations.github_knowledge import search_github_knowledge

logger = logging.getLogger(__name__)
_cache = {}


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


async def search_all(query: str, limit: int = 8) -> list:
    """
    Run OpenAlex, arXiv, and GitHub knowledge search in parallel.
    Merges, deduplicates, and returns sorted results.
    - OpenAlex: sorted by citation count (most cited first)
    - arXiv: sorted by relevance / newest
    - GitHub: matched links from awesome repos
    """
    cache_key = f"{query}_{limit}"
    now = time.time()
    if cache_key in _cache:
        cached_data, timestamp = _cache[cache_key]
        if now - timestamp < 600:  # 10 minutes TTL
            logger.info(f"Returning cached literature results for {query}")
            return cached_data

    results = await asyncio.gather(
        openalex_search(query, limit=limit),
        arxiv_search(query, limit=limit),
        asyncio.to_thread(search_github_knowledge, query),
        s2_search(query, limit=limit),
        crossref_search(query, limit=limit),
        return_exceptions=True
    )

    def _handle_res(res, name):
        if isinstance(res, Exception):
            logger.error(f"Gather Error ({name}): {res}")
            return []
        return res

    openalex_results = _handle_res(results[0], "OpenAlex")
    arxiv_results = _handle_res(results[1], "arXiv")
    github_results = _handle_res(results[2], "GitHub")
    s2_results = _handle_res(results[3], "SemanticScholar")
    crossref_results = _handle_res(results[4], "Crossref")

    # Tag sources that don't already have one
    for p in openalex_results:
        p.setdefault("source", "OpenAlex")
    for p in arxiv_results:
        p.setdefault("source", "arXiv")
    for p in github_results:
        p.setdefault("source", p.get("source", "GitHub"))
    for p in crossref_results:
        p.setdefault("source", "Crossref")
    # Semantic Scholar already tags its own in semanticscholar.py

    # Merge: Semantic Scholar first (highest relevance), then OpenAlex, then Crossref, then arXiv, then GitHub
    merged = s2_results + openalex_results + crossref_results + arxiv_results + github_results

    # Deduplicate
    unique = _deduplicate(merged)
    
    _cache[cache_key] = (unique, now)
    return unique
