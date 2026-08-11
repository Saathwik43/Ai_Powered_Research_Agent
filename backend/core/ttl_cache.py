"""
Bounded TTL cache.

The search path's caches were plain dicts that were never evicted: search
results, paper embeddings (3072 floats each, 30 per search) and relevance
verdicts all accumulated for the lifetime of the process. Expiry was checked on
read, so an entry nobody looked up again was retained forever.

``TTLCache`` behaves like a dict for the access patterns already in use
(``in``, ``[]``, ``get``, ``pop``, ``del``, ``clear``, ``len``) so it can be
dropped in where those dicts were, but it expires on read *and* evicts the
oldest entries once ``maxsize`` is exceeded.

Single-process only. Making the store pluggable is the seam where a Redis
backend goes when the deployment runs more than one worker — the call sites
already speak only this interface.
"""

import time
from collections import OrderedDict


class TTLCache:
    """Mapping with per-entry expiry and a hard capacity ceiling."""

    __slots__ = ("_data", "_ttl", "_maxsize", "_hits", "_misses", "_evictions")

    def __init__(self, maxsize: int, ttl: float):
        self._data: OrderedDict = OrderedDict()
        self._ttl = ttl
        self._maxsize = maxsize
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ── dict-compatible surface ──────────────────────────────────────────────

    def __contains__(self, key) -> bool:
        return self._live(key) is not _MISSING

    def __getitem__(self, key):
        value = self._live(key)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value) -> None:
        self._data[key] = (value, time.time())
        self._data.move_to_end(key)
        self._evict()

    def __delitem__(self, key) -> None:
        del self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key, default=None):
        value = self._live(key)
        return default if value is _MISSING else value

    def setdefault(self, key, default):
        value = self._live(key)
        if value is _MISSING:
            self[key] = default
            return default
        return value

    def pop(self, key, default=None):
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        value, stored_at = entry
        return default if self._expired(stored_at) else value

    def clear(self) -> None:
        self._data.clear()

    def keys(self):
        return list(self._data.keys())

    def values(self):
        """Live values only — expired entries are skipped, not returned."""
        now = time.time()
        return [v for v, stored_at in self._data.values() if (now - stored_at) < self._ttl]

    def items(self):
        now = time.time()
        return [(k, v) for k, (v, stored_at) in self._data.items() if (now - stored_at) < self._ttl]

    # ── internals ────────────────────────────────────────────────────────────

    def _expired(self, stored_at: float) -> bool:
        return (time.time() - stored_at) >= self._ttl

    def _live(self, key):
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return _MISSING
        value, stored_at = entry
        if self._expired(stored_at):
            del self._data[key]
            self._misses += 1
            return _MISSING
        # Recency counts for eviction, so a hit is a use.
        self._data.move_to_end(key)
        self._hits += 1
        return value

    def _evict(self) -> None:
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)  # oldest by last use
            self._evictions += 1

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "entries": len(self._data),
            "maxsize": self._maxsize,
            "ttl": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total else 0.0,
            "evictions": self._evictions,
        }


class _Missing:
    __slots__ = ()


_MISSING = _Missing()
