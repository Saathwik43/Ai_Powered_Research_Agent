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

    tasks = {
        asyncio.create_task(openalex_search(query, limit=limit), name="OpenAlex"),
        asyncio.create_task(arxiv_search(query, limit=limit), name="arXiv"),
        asyncio.create_task(asyncio.to_thread(search_github_knowledge, query), name="GitHub"),
        asyncio.create_task(s2_search(query, limit=limit), name="SemanticScholar"),
        asyncio.create_task(crossref_search(query, limit=limit), name="Crossref")
    }

    done, pending = await asyncio.wait(tasks, timeout=6.0)
    
    for p in pending:
        p.cancel()
        logger.warning(f"Search task timed out: {p.get_name()}")

    results_map = {}
    for task in done:
        try:
            results_map[task.get_name()] = task.result()
        except Exception as e:
            logger.error(f"Search task failed ({task.get_name()}): {e}")
            results_map[task.get_name()] = []
            
    openalex_results = results_map.get("OpenAlex", [])
    arxiv_results = results_map.get("arXiv", [])
    github_results = results_map.get("GitHub", [])
    s2_results = results_map.get("SemanticScholar", [])
    crossref_results = results_map.get("Crossref", [])

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
