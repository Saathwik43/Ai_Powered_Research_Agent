"""
Shared paper relevance filtering used by both manuscript generation and
the /api/literature endpoint.

Fail-open behaviour note
------------------------
When the Groq classification call fails (rate limit, timeout, network error),
the paper is **included** by default.  This is intentional for manuscript
generation (better to surface a possibly-borderline paper than silently drop
context), but carries a known trade-off for the literature endpoint: under
heavy Groq load every unclassified paper leaks through, potentially returning
noisy results to the user.  The trade-off is documented here pending a
frontend 'low-confidence' flag or server-side fail-closed option.
"""

import logging
import time
from ai.llm_provider import generate_completion

logger = logging.getLogger(__name__)

__all__ = ["_filter_relevant_papers"]

# In-memory relevance classification cache.
# Key: (topic_lower, normalised_title_prefix)  →  Value: (is_relevant: bool, timestamp)
# TTL: 600s (10 minutes), matching search_all's cache TTL.
_relevance_cache: dict[tuple, tuple] = {}
_CACHE_TTL = 600


def _cache_key(topic: str, paper: dict) -> tuple:
    """Stable cache key from topic + first 60 chars of normalised title."""
    import re
    title = re.sub(r"[^a-z0-9 ]", "", (paper.get("title", "") or "").lower()).strip()[:60]
    return (topic.strip().lower(), title)


async def _filter_relevant_papers(topic: str, papers: list) -> list:
    """
    Filter *papers* by relevance to *topic*.

    Fast-path: if a paper already carries a ``relevance_score`` field
    (e.g. from Semantic Scholar), papers with score < 0.5 are dropped
    without an LLM call.

    Cache-path: if a previous call already classified this (topic, paper)
    pair within the last 10 minutes, the cached verdict is reused.

    LLM-path: for all other papers a single yes/no call is made to the
    configured provider (Groq in auto mode).  On failure the paper is
    **included** (fail-open).

    Parameters
    ----------
    topic : str
        The research topic the papers must be relevant to.
    papers : list[dict]
        Raw paper dicts as returned by ``search_all()``.

    Returns
    -------
    list[dict]
        Subset of *papers* deemed relevant.
    """
    import asyncio
    now = time.time()
    
    async def _process_paper(paper):
        # Fast-path: check if provider already scored relevance
        if "relevance_score" in paper:
            if paper["relevance_score"] >= 0.5:
                return paper
            else:
                logger.info(
                    f"Filtered out low-relevance paper "
                    f"(score={paper['relevance_score']}): {paper.get('title', '')}"
                )
            return None

        # Cache-path: reuse a recent classification verdict
        ck = _cache_key(topic, paper)
        if ck in _relevance_cache:
            cached_relevant, cached_at = _relevance_cache[ck]
            if now - cached_at < _CACHE_TTL:
                if cached_relevant:
                    return paper
                else:
                    logger.info(f"Filtered out irrelevant paper (cached): {paper.get('title', '')}")
                return None
            else:
                del _relevance_cache[ck]  # expired

        # LLM-path: single-call relevance classification
        title = paper.get("title", "")
        abstract = (paper.get("abstract", "") or "")[:300]
        try:
            answer = await generate_completion(
                system_prompt="You are a research relevance classifier. Answer only 'yes' or 'no'.",
                user_prompt=(
                    f'Is the following paper relevant to the research topic "{topic}"?\n'
                    f'Paper title: "{title}"\n'
                    f'Paper abstract: "{abstract}"\n'
                    'Answer with exactly "yes" or "no".'
                ),
                max_tokens=5,
                temperature=0.0,
            )
            is_relevant = answer.strip().lower().startswith("yes")
            _relevance_cache[ck] = (is_relevant, now)
            if is_relevant:
                return paper
            else:
                logger.info(f"Filtered out irrelevant paper: {title}")
                return None
        except Exception as e:
            logger.warning(
                f"Relevance check failed for '{title}', including by default: {e}"
            )
            return paper  # fail-open: include if classification fails

    processed_papers = await asyncio.gather(*[_process_paper(p) for p in papers])
    relevant = [p for p in processed_papers if p is not None]
    return relevant

