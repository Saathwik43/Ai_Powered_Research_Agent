"""
Recent successful queries, for autocomplete.

The search box used to filter a hardcoded 16-item array with
``String.includes``, which meant "CNN" and "ML" matched nothing at all and no
suggestion ever reflected what this deployment is actually used for.

Only queries that returned results are recorded — suggesting a query that
found nothing is worse than suggesting nothing. Deliberately not per-user: the
value of an autocomplete list is that it is shared, and a query someone
submitted is not private the way their saved surveys are. Nothing here is
attributed to a user.
"""

import logging
import re
import time

from core import shared_store
from core.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# Recency-ordered, capacity-bounded. The TTL is long because a suggestion list
# that forgets yesterday's work is not much of a suggestion list.
_MAX_QUERIES = 300
_TTL_SECONDS = 7 * 24 * 3600

_history = TTLCache(maxsize=_MAX_QUERIES, ttl=_TTL_SECONDS)

_WHITESPACE = re.compile(r"\s+")
_SUGGEST_MAX_LEN = 80


def normalize_suggest_input(text: str) -> str:
    """Lowercase, whitespace-collapsed prefix for matching."""
    return _WHITESPACE.sub(" ", str(text or "")).strip().lower()


def record_query(query: str) -> None:
    """Remember *query* as a suggestion candidate. Never raises."""
    cleaned = _WHITESPACE.sub(" ", str(query or "")).strip()
    if not cleaned or len(cleaned) > _SUGGEST_MAX_LEN:
        return
    # Key on the lowercase form so casing variants collapse; keep the first
    # spelling seen for display.
    key = cleaned.lower()
    if key not in _history:
        _history[key] = cleaned


async def record_query_shared(query: str) -> None:
    """:func:`record_query`, also written through to the durable tier (1.2).

    A suggestion list is only useful because it is shared — "what has this
    deployment actually been used for". Holding it in one worker's memory made
    it neither shared between workers nor durable across a deploy, which is the
    same list of queries being forgotten twice.
    """
    cleaned = _WHITESPACE.sub(" ", str(query or "")).strip()
    if not cleaned or len(cleaned) > _SUGGEST_MAX_LEN:
        return
    record_query(cleaned)
    await shared_store.set(
        _NS, cleaned.lower(), cleaned, _TTL_SECONDS, tag=_TAG,
    )


def recent_queries() -> list[str]:
    """Most-recently-used first."""
    return list(reversed(_history.values()))


async def recent_queries_shared() -> list[str]:
    """:func:`recent_queries`, warming this worker from the durable tier first."""
    await _hydrate()
    return recent_queries()


_NS = "suggest"
_TAG = "queries"

# The durable list is pulled in once and then left alone: /api/suggest fires on
# every keystroke behind a 250ms debounce, so a Mongo query per call would cost
# far more than the suggestion is worth.
_HYDRATE_INTERVAL = 300.0
_hydrated_at = 0.0


async def _hydrate() -> None:
    global _hydrated_at
    now = time.time()
    if now - _hydrated_at < _HYDRATE_INTERVAL:
        return
    _hydrated_at = now
    values = await shared_store.scan_tag(_NS, _TAG, limit=_MAX_QUERIES)
    restored = 0
    for phrase in values:
        if isinstance(phrase, str) and phrase and phrase.lower() not in _history:
            _history[phrase.lower()] = phrase
            restored += 1
    if restored:
        logger.info("Suggestion history hydrated with %d stored quer(ies)", restored)


def clear() -> None:
    global _hydrated_at
    _history.clear()
    _hydrated_at = 0.0


def _suggest_rank(prefix: str, phrase: str) -> int | None:
    """
    Match tier for *phrase* against *prefix*, or None for no match.

    Lower is better. Tiers are ordered by how strongly the match signals what
    the user meant, so an acronym expansion never outranks a literal prefix.
    """
    lowered = phrase.lower()
    words = lowered.split()

    if lowered.startswith(prefix):
        return 0
    if any(word.startswith(prefix) for word in words):
        return 1
    # Initials: "nas" → "neural architecture search". This is the case plain
    # substring matching could never handle.
    initials = "".join(word[0] for word in words if word)
    if len(prefix) >= 2 and initials.startswith(prefix):
        return 2
    if prefix in lowered:
        return 3
    return None
