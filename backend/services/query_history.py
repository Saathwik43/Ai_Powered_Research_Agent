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

import re

from core.ttl_cache import TTLCache

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


def recent_queries() -> list[str]:
    """Most-recently-used first."""
    return list(reversed(_history.values()))


def clear() -> None:
    _history.clear()


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
