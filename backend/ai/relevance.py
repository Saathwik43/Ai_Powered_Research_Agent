"""
Shared paper relevance filtering used by both manuscript generation and
the /api/literature endpoint.

Relevance classification
-------------------------
Each paper without a pre-existing ``relevance_score`` gets a single yes/no
LLM classification call (via the existing provider cascade — Groq by
default) judging whether its title+abstract is actually relevant to the
requested topic. This catches synonyms/paraphrases and off-topic noise
that plain keyword overlap misses (e.g. a paper about "network intrusion"
correctly matching a "cybersecurity threat detection" query even with no
literal word overlap).

Fail-open behaviour note
------------------------
When the classification call fails (rate limit, timeout, network error),
the paper is **included** by default. This is intentional for manuscript
generation (better to surface a possibly-borderline paper than silently
drop context) and for the literature endpoint (better to show more results
than none when the classifier is down).
"""

import logging
import time
import re

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
    title = re.sub(r"[^a-z0-9 ]", "", (paper.get("title", "") or "").lower()).strip()[:60]
    return (topic.strip().lower(), title)


_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a strict research-paper relevance classifier. Given a research "
    "topic and a paper's title and abstract, respond with exactly one word: "
    "'yes' if the paper is genuinely relevant to the topic, or 'no' if it is "
    "not. Do not explain, do not add punctuation, respond with only yes or no."
)


def _build_classifier_prompt(topic: str, title: str, abstract: str) -> str:
    return (
        f"Research topic: {topic}\n\n"
        f"Paper title: {title}\n"
        f"Paper abstract: {abstract}\n\n"
        "Is this paper relevant to the research topic? Answer yes or no."
    )


async def _classify_relevance(topic: str, title: str, abstract: str) -> bool:
    """Single yes/no LLM call. Raises on failure — caller decides fail-open."""
    user_prompt = _build_classifier_prompt(topic, title, abstract)
    # Pin to Groq: auto cascade was OpenAI→Gemini→Groq for every paper, so a
    # Survey limit=100 burned ~100 OpenAI + ~200 Gemini attempts before Groq
    # answered. Yes/no classification does not need the expensive providers.
    result = await generate_completion(
        _CLASSIFIER_SYSTEM_PROMPT,
        user_prompt,
        max_tokens=5,
        temperature=0.0,
        provider_override="groq",
    )
    return (result or "").strip().lower().startswith("yes")


async def _filter_relevant_papers(topic: str, papers: list) -> list:
    """
    Filter *papers* by relevance to *topic*.

    Fast-path: if a paper already carries a ``relevance_score`` field
    (e.g. from Semantic Scholar), papers with score < 0.5 are dropped
    without an LLM call.

    Cache-path: if a previous call already classified this (topic, paper)
    pair within the last 10 minutes, the cached verdict is reused.

    LLM-path: for all other papers a single yes/no call is made to the
    configured provider (Groq in auto mode). On failure the paper is
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

        # LLM-path: single yes/no classification call
        title = paper.get("title", "")
        abstract = (paper.get("abstract", "") or "")[:600]
        try:
            is_relevant = await _classify_relevance(topic, title, abstract)
        except Exception as e:
            logger.warning(f"Relevance classification failed, including paper (fail-open): {e}")
            return paper  # fail-open: don't cache a failure verdict

        _relevance_cache[ck] = (is_relevant, now)
        if is_relevant:
            return paper
        else:
            logger.info(f"Filtered out irrelevant paper: {title}")
            return None

    from ai.llm_provider import global_llm_sem
    async def _throttled(p):
        async with global_llm_sem:
            return await _process_paper(p)

    processed_papers = await asyncio.gather(*[_throttled(p) for p in papers])
    relevant = [p for p in processed_papers if p is not None]
    return relevant
