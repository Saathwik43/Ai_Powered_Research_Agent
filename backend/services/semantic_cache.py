"""
Semantic result cache — reuse a search when the *meaning* already matches.

Phase 1 (``core.query_key``) collapses queries that differ only in wording:
case, spacing, punctuation, plurals, word order. It cannot collapse synonyms.
"CNN classification" and "convolutional neural network classification" are one
search to a researcher and two full 11-source fan-outs to the canonical key.

This module closes that gap by keying on the query *embedding* instead of the
query string, and serving a stored result when the cosine similarity is high
enough.

Three tiers, deliberately conservative
--------------------------------------
An exact-string cache can never be wrong. A semantic cache is a guess, so the
tiers trade hit rate against how much of the stored result is trusted:

    cosine >= VERBATIM_THRESHOLD (0.97)  serve the stored ranking untouched
    cosine >= RERANK_THRESHOLD   (0.94)  reuse the stored *papers*, but re-rank
                                         them against the query actually typed
    below                                miss — run the real search

The middle tier is where most of the value is. Re-ranking costs no network
call (paper embeddings are already cached by content digest) and guarantees
the ordering matches what the user asked for, so a near-miss degrades into
"slightly different candidate pool" rather than "answers to someone else's
question".

Disclosure is mandatory
-----------------------
Every hit carries ``matched_query``. Callers must show it. A cached answer to
a question the user did not ask is only acceptable when the user can see the
substitution and override it — silent substitution is the failure mode that
makes a research tool untrustworthy.
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Serve the stored ranking as-is at or above this similarity.
VERBATIM_THRESHOLD = 0.97
# Between this and VERBATIM_THRESHOLD, reuse the papers but re-rank them.
RERANK_THRESHOLD = 0.94

TTL_SECONDS = 600

# Lookup is a linear scan of stored query embeddings, so the cache is capped.
# 200 entries is a few MB and scans in well under a millisecond.
MAX_ENTRIES = 200

MODE_VERBATIM = "verbatim"
MODE_RERANK = "rerank"


@dataclass(frozen=True)
class SemanticHit:
    """A stored result deemed close enough to answer *this* query."""

    papers: list
    matched_query: str
    similarity: float
    mode: str  # MODE_VERBATIM | MODE_RERANK

    @property
    def needs_rerank(self) -> bool:
        return self.mode == MODE_RERANK


# bucket -> {cache_key: (embedding, papers, display_query, stored_at)}
# Bucketed by the caller's fan-out parameters so a limit=5 search can never be
# served from a limit=50 entry.
_store: dict[str, dict[str, tuple]] = {}


def _cosine(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _prune(bucket: dict, now: float) -> None:
    for key in [k for k, (_, _, _, stored_at) in bucket.items() if now - stored_at >= TTL_SECONDS]:
        del bucket[key]
    while len(bucket) > MAX_ENTRIES:
        oldest = min(bucket, key=lambda k: bucket[k][3])
        del bucket[oldest]


def lookup(bucket_key: str, cache_key: str, query_embedding: list) -> SemanticHit | None:
    """
    Closest stored result for *query_embedding*, or None below threshold.

    *cache_key* is the caller's exact canonical key and is skipped during the
    scan: an exact hit is the exact cache's job, and matching a query against
    itself would report a bogus semantic hit.
    """
    if not query_embedding:
        return None

    bucket = _store.get(bucket_key)
    if not bucket:
        return None

    now = time.time()
    _prune(bucket, now)

    best_similarity = 0.0
    best_entry = None
    for key, (embedding, papers, display_query, _stored_at) in bucket.items():
        if key == cache_key:
            continue
        similarity = _cosine(query_embedding, embedding)
        if similarity > best_similarity:
            best_similarity, best_entry = similarity, (papers, display_query)

    if best_entry is None or best_similarity < RERANK_THRESHOLD:
        return None

    papers, display_query = best_entry
    mode = MODE_VERBATIM if best_similarity >= VERBATIM_THRESHOLD else MODE_RERANK
    logger.info(
        "Semantic cache %s hit (%.4f): serving '%s'", mode, best_similarity, display_query
    )
    # Copy one level deep: the caller ranks and mutates these dicts, and the
    # stored entry has to stay pristine for the next lookup.
    return SemanticHit(
        papers=[dict(p) for p in papers],
        matched_query=display_query,
        similarity=best_similarity,
        mode=mode,
    )


def store(bucket_key: str, cache_key: str, query_embedding: list, display_query: str, papers: list) -> None:
    """Remember *papers* as the answer to *display_query*. No-op without an embedding."""
    if not query_embedding or not papers:
        return
    bucket = _store.setdefault(bucket_key, {})
    bucket[cache_key] = (query_embedding, [dict(p) for p in papers], display_query, time.time())
    _prune(bucket, time.time())


def clear() -> None:
    """Drop everything. For tests and admin cache-flush paths."""
    _store.clear()


def stats() -> dict:
    return {
        "buckets": len(_store),
        "entries": sum(len(b) for b in _store.values()),
        "rerank_threshold": RERANK_THRESHOLD,
        "verbatim_threshold": VERBATIM_THRESHOLD,
    }
