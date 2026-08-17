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
from core.paper_identity import paper_identity
from core.query_key import canonical_key
from core.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

__all__ = ["_filter_relevant_papers"]

# In-memory relevance classification cache.
# Key: (canonical_topic, paper_identity)  →  Value: (is_relevant: bool, timestamp)
# TTL: 600s (10 minutes), matching search_all's cache TTL.
# Bounded: the TTL below is the authoritative freshness gate, the cache's
# own retention window is a little longer so nothing expires mid-check.
_relevance_cache = TTLCache(maxsize=20000, ttl=900)
_CACHE_TTL = 600

# Classifier failures are cached too, briefly. Without this a rate-limited
# provider is re-hammered on every retry: 100 papers means 100 failing calls,
# fail-open, and 100 more on the next identical request. Caching the failure
# sheds that load while keeping the fail-open result, and the short TTL means
# a recovered provider is picked up within the minute.
_failure_cache = TTLCache(maxsize=20000, ttl=300)
_FAILURE_TTL = 60


def _cache_key(topic: str, paper: dict) -> tuple:
    """
    Stable cache key from canonical topic + full paper identity.

    The topic half uses the same canonicalisation as search_all's cache key, so
    a verdict computed for "Machine Learning" is reused for "machine learning"
    instead of being re-billed as a fresh LLM call.

    The paper half is ``core.paper_identity`` — DOI, else arXiv id, else the
    full normalised title — the same notion of identity the dedupe index uses.
    It was a 60-character title prefix, so two genuinely different papers
    sharing that prefix ("…for Medical Image Segmentation Part I" / "Part II")
    shared one yes/no verdict, and the second was included or dropped on the
    first one's classification (audit A5).
    """
    return (canonical_key(topic), paper_identity(paper))


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


# ─── Batched classification ───────────────────────────────────────────────────
#
# One paper per call was the single largest cost in the search path: 100 papers
# through a Semaphore(3) is ~34 serial round-trips. Judging a page of papers in
# one prompt turns that into 4. The batch is kept small so a single malformed
# reply cannot poison many verdicts, and so the prompt stays well inside the
# context window even with long abstracts.
BATCH_SIZE = 10
_ABSTRACT_CHARS = 400

_BATCH_SYSTEM_PROMPT = (
    "You are a strict research-paper relevance classifier. You are given a "
    "research topic and a numbered list of papers. For EACH paper, decide "
    "whether it is genuinely relevant to the topic.\n"
    "Respond with one line per paper, in the same order, formatted exactly as "
    "'<number>: yes' or '<number>: no'. Output nothing else — no preamble, no "
    "explanation, no blank lines."
)


def _build_batch_prompt(topic: str, papers: list) -> str:
    entries = []
    for index, paper in enumerate(papers, start=1):
        title = paper.get("title", "") or "Untitled"
        abstract = (paper.get("abstract", "") or "")[:_ABSTRACT_CHARS]
        entries.append(f"{index}. Title: {title}\n   Abstract: {abstract}")
    listing = "\n\n".join(entries)
    return (
        f"Research topic: {topic}\n\n"
        f"Papers:\n{listing}\n\n"
        f"Answer with {len(papers)} lines, '<number>: yes' or '<number>: no'."
    )


_VERDICT_RE = re.compile(r"^\s*(\d+)\s*[:.)-]\s*(yes|no)\b", re.IGNORECASE | re.MULTILINE)


def _parse_batch_verdicts(reply: str, expected: int) -> list[bool] | None:
    """
    Verdicts from a batch reply, or None if it cannot be trusted.

    Returning None rather than guessing matters: a partial or misaligned parse
    would silently assign one paper's verdict to another. The caller falls back
    to per-paper calls, which is slower but never wrong.
    """
    verdicts: dict[int, bool] = {}
    for number, answer in _VERDICT_RE.findall(reply or ""):
        position = int(number)
        if 1 <= position <= expected:
            verdicts[position] = answer.lower() == "yes"

    if len(verdicts) != expected:
        return None
    return [verdicts[i] for i in range(1, expected + 1)]


async def _classify_batch(topic: str, papers: list) -> list[bool] | None:
    """One call for *papers*. None when the reply could not be parsed."""
    reply = await generate_completion(
        _BATCH_SYSTEM_PROMPT,
        _build_batch_prompt(topic, papers),
        # ~6 tokens per verdict line, plus headroom.
        max_tokens=16 * len(papers),
        temperature=0.0,
        provider_override="groq",
    )
    return _parse_batch_verdicts(reply, len(papers))


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

    def _resolve_without_llm(paper) -> bool | None:
        """Verdict from score/cache/recent-failure, or None if a call is needed."""
        # Fast-path: the provider already scored relevance.
        if "relevance_score" in paper:
            if paper["relevance_score"] >= 0.5:
                return True
            logger.info(
                f"Filtered out low-relevance paper "
                f"(score={paper['relevance_score']}): {paper.get('title', '')}"
            )
            return False

        ck = _cache_key(topic, paper)

        # Cache-path: reuse a recent classification verdict.
        if ck in _relevance_cache:
            cached_relevant, cached_at = _relevance_cache[ck]
            if now - cached_at < _CACHE_TTL:
                if not cached_relevant:
                    logger.info(f"Filtered out irrelevant paper (cached): {paper.get('title', '')}")
                return cached_relevant
            del _relevance_cache[ck]  # expired

        # Failure-path: a recent failure means the classifier is down. Reuse
        # the fail-open verdict instead of paying for another call.
        failed_at = _failure_cache.get(ck)
        if failed_at is not None:
            if now - failed_at < _FAILURE_TTL:
                return True
            del _failure_cache[ck]

        return None

    verdicts: dict[int, bool] = {}
    undecided: list[int] = []
    for position, paper in enumerate(papers):
        resolved = _resolve_without_llm(paper)
        if resolved is None:
            undecided.append(position)
        else:
            verdicts[position] = resolved

    async def _classify_chunk(positions: list[int]) -> None:
        """Judge one chunk, recording verdicts and honouring fail-open."""
        chunk = [papers[i] for i in positions]
        try:
            batch = await _classify_batch(topic, chunk)
        except Exception as e:
            # Provider failure: include everything and remember, so a
            # rate-limited provider is not re-hammered on the next request.
            logger.warning(f"Relevance classification failed, including papers (fail-open): {e}")
            for i, paper in zip(positions, chunk):
                verdicts[i] = True
                _failure_cache[_cache_key(topic, paper)] = now
            return

        if batch is None:
            # Reply could not be aligned to the papers. Falling back to
            # per-paper calls is slower but cannot mis-assign a verdict.
            logger.warning(
                f"Batch classifier reply unparseable for {len(chunk)} paper(s); "
                "falling back to per-paper classification."
            )
            batch = []
            for paper in chunk:
                try:
                    batch.append(await _classify_relevance(
                        topic, paper.get("title", ""), (paper.get("abstract", "") or "")[:600]
                    ))
                except Exception as e:
                    logger.warning(f"Per-paper classification failed, including paper: {e}")
                    _failure_cache[_cache_key(topic, paper)] = now
                    batch.append(True)

        for i, paper, is_relevant in zip(positions, chunk, batch):
            verdicts[i] = is_relevant
            ck = _cache_key(topic, paper)
            _failure_cache.pop(ck, None)
            _relevance_cache[ck] = (is_relevant, now)
            if not is_relevant:
                logger.info(f"Filtered out irrelevant paper: {paper.get('title', '')}")

    from ai.llm_provider import global_llm_sem

    async def _throttled(positions: list[int]) -> None:
        async with global_llm_sem:
            await _classify_chunk(positions)

    chunks = [undecided[i:i + BATCH_SIZE] for i in range(0, len(undecided), BATCH_SIZE)]
    if chunks:
        await asyncio.gather(*[_throttled(c) for c in chunks])

    return [paper for position, paper in enumerate(papers) if verdicts.get(position, True)]
