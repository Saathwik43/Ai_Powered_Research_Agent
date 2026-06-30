import asyncio
from integrations.openalex import search_papers as openalex_search
from integrations.arxiv import search_papers as arxiv_search
from integrations.semanticscholar import search_papers as s2_search
from integrations.github_knowledge import search_github_knowledge


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
    openalex_results, arxiv_results, github_results, s2_results = await asyncio.gather(
        openalex_search(query, limit=limit),
        arxiv_search(query, limit=limit),
        asyncio.to_thread(search_github_knowledge, query),
        s2_search(query, limit=limit)
    )

    # Tag sources that don't already have one
    for p in openalex_results:
        p.setdefault("source", "OpenAlex")
    for p in arxiv_results:
        p.setdefault("source", "arXiv")
    for p in github_results:
        p.setdefault("source", p.get("source", "GitHub"))
    # Semantic Scholar already tags its own in semanticscholar.py

    # Merge: Semantic Scholar first (highest relevance), then OpenAlex, then arXiv, then GitHub
    merged = s2_results + openalex_results + arxiv_results + github_results

    # Deduplicate
    unique = _deduplicate(merged)

    return unique
