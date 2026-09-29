"""
Cross-process cache tier (1.2 / 1.3).

Every cache on the search path is a per-worker dict: ``TTLCache`` for search
results and relevance verdicts, a module-level dict for the semantic cache,
another for paper embeddings. All of them die with the process, so a deploy or a
second uvicorn worker starts cold — and cold means re-running a 9-source fan-out
and re-paying for embeddings that were computed minutes earlier on the other
worker.

This module is the second tier those caches fall through to. Mongo, not Redis:
the deployment already has one, a TTL index does the eviction, and nothing new
has to be provisioned for a cache to survive a restart. The surface is
deliberately narrow — get/set/get_many/set_many — so a Redis backend is a swap
of the private ``_backend_*`` functions, which is the seam ``TTLCache``'s
docstring already promised.

Two collections, because they expire differently:

``cache_entries``
    Search results, semantic-cache entries, relevance verdicts. TTL-indexed on
    ``exp``; Mongo deletes them.

``paper_embeddings``
    Vectors keyed by paper identity (DOI → arXiv id → title). **No TTL** — a
    paper's embedding does not go stale, and re-embedding the same paper is
    exactly the cost 1.3 exists to stop paying.

Never raises, never blocks for long. A cache is an optimisation: if Mongo is
slow or down every call here degrades to "miss" and the caller does the real
work. Consecutive failures trip a breaker, so a broken store costs one timeout
every few minutes rather than one on every lookup.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Namespaces. Kept here rather than at the call sites so one file lists
# everything that can be in the shared cache.
NS_SEARCH = "search"          # ranked fan-out results, keyed by canonical query
NS_SEMANTIC = "semantic"      # semantic-cache entries, keyed by bucket + query
NS_RELEVANCE = "relevance"    # LLM yes/no verdicts, keyed by topic + paper id
NS_BRIEF = "brief"            # card briefing bullets, keyed by paper id
NS_SNOWBALL = "snowball"      # citation expansion (2.2), keyed by seed set + direction

# A cache read must never be slower than the work it is meant to skip.
_OP_TIMEOUT = float(os.getenv("SHARED_CACHE_TIMEOUT", "2.5"))

# Mongo's own document ceiling is 16MB. This is well under it: anything bigger
# is a search result set nobody wants to ship over the wire twice anyway.
_MAX_VALUE_BYTES = 4 * 1024 * 1024

# _id has to stay indexable, and a bucketed cache key plus a long query does not
# have a bounded length. Longer keys are digested.
_MAX_INLINE_KEY = 160

# Breaker: after this many consecutive failures the store is skipped entirely
# until the cooldown expires.
_FAILURE_THRESHOLD = 3
_COOLDOWN_SECONDS = 300.0

_consecutive_failures = 0
_skip_until = 0.0
_hits = 0
_misses = 0
_errors = 0


def _env_enabled() -> bool:
    raw = (os.getenv("SHARED_CACHE_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


_enabled = _env_enabled()


def enabled() -> bool:
    """False when switched off, or while the failure breaker is open."""
    if not _enabled:
        return False
    return time.time() >= _skip_until


def set_enabled(value: bool) -> None:
    """Turn the tier on/off at runtime. The test suite switches it off."""
    global _enabled, _consecutive_failures, _skip_until
    _enabled = bool(value)
    _consecutive_failures = 0
    _skip_until = 0.0


def _record_ok() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


def _record_failure(operation: str, exc: Exception) -> None:
    global _consecutive_failures, _skip_until, _errors
    _errors += 1
    _consecutive_failures += 1
    if _consecutive_failures >= _FAILURE_THRESHOLD:
        _skip_until = time.time() + _COOLDOWN_SECONDS
        logger.warning(
            "Shared cache disabled for %.0fs after %d consecutive failures (%s: %s)",
            _COOLDOWN_SECONDS, _consecutive_failures, operation, exc,
        )
    else:
        logger.debug("Shared cache %s failed: %s", operation, exc)


async def _guard(operation: str, coro, default=None):
    """Run *coro* under a timeout, converting any failure into *default*."""
    try:
        result = await asyncio.wait_for(coro, timeout=_OP_TIMEOUT)
    except Exception as exc:  # includes asyncio.TimeoutError
        _record_failure(operation, exc)
        return default
    _record_ok()
    return result


def _doc_id(namespace: str, key: str) -> str:
    key = key or ""
    if len(key) > _MAX_INLINE_KEY:
        key = "h:" + hashlib.blake2b(key.encode("utf-8"), digest_size=20).hexdigest()
    return f"{namespace}:{key}"


def _encode(value) -> str | None:
    """JSON, not raw BSON: a paper dict can carry a key with a '.' in it, which
    Mongo rejects as a field name. Returns None when the value is too big."""
    try:
        blob = json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as exc:
        logger.debug("Shared cache value not serialisable: %s", exc)
        return None
    if len(blob.encode("utf-8", "ignore")) > _MAX_VALUE_BYTES:
        return None
    return blob


def _decode(blob):
    if not isinstance(blob, str):
        return None
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return None


def _collection(name: str):
    from core.database import db
    return db[name]


# ─── Generic key/value with TTL ────────────────────────────────────────────────

async def get(namespace: str, key: str):
    """Stored value for *key*, or None on a miss / any failure."""
    global _hits, _misses
    if not enabled():
        return None
    doc = await _guard(
        "get",
        _collection("cache_entries").find_one({"_id": _doc_id(namespace, key)}, {"v": 1, "exp": 1}),
    )
    if not doc:
        _misses += 1
        return None
    # TTL deletion is a background sweep on a ~60s cycle, so an expired document
    # can still be read. Check the expiry here rather than trusting the sweep.
    if _is_expired(doc):
        _misses += 1
        return None
    _hits += 1
    return _decode(doc.get("v"))


async def get_many(namespace: str, keys) -> dict:
    """``{key: value}`` for the keys that are present. One round trip."""
    global _hits, _misses
    keys = list(keys or [])
    if not enabled() or not keys:
        return {}
    by_doc_id = {_doc_id(namespace, k): k for k in keys}
    cursor = _collection("cache_entries").find(
        {"_id": {"$in": list(by_doc_id)}}, {"v": 1, "exp": 1}
    )
    docs = await _guard("get_many", cursor.to_list(length=len(by_doc_id)), default=[])
    out = {}
    for doc in docs or []:
        if _is_expired(doc):
            continue
        value = _decode(doc.get("v"))
        if value is not None:
            out[by_doc_id[doc["_id"]]] = value
    _hits += len(out)
    _misses += max(0, len(keys) - len(out))
    return out


def _is_expired(doc: dict) -> bool:
    exp = doc.get("exp")
    if not isinstance(exp, datetime):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= datetime.now(timezone.utc)


async def set(namespace: str, key: str, value, ttl_seconds: float, tag: str | None = None) -> None:
    """Store *value* under *key* for *ttl_seconds*. Silent no-op on failure.

    *tag* groups entries that are read back as a set rather than by key — see
    :func:`scan_tag`.
    """
    if not enabled():
        return
    blob = _encode(value)
    if blob is None:
        return
    exp = datetime.now(timezone.utc) + timedelta(seconds=max(1.0, ttl_seconds))
    fields = {"v": blob, "exp": exp, "ns": namespace}
    if tag:
        fields["tag"] = tag
    await _guard(
        "set",
        _collection("cache_entries").update_one(
            {"_id": _doc_id(namespace, key)}, {"$set": fields}, upsert=True,
        ),
    )


async def set_many(namespace: str, items: dict, ttl_seconds: float, tag: str | None = None) -> None:
    """Bulk form of :func:`set`. One round trip for the whole mapping."""
    if not enabled() or not items:
        return
    from pymongo import UpdateOne

    exp = datetime.now(timezone.utc) + timedelta(seconds=max(1.0, ttl_seconds))
    operations = []
    for key, value in items.items():
        blob = _encode(value)
        if blob is None:
            continue
        fields = {"v": blob, "exp": exp, "ns": namespace}
        if tag:
            fields["tag"] = tag
        operations.append(UpdateOne(
            {"_id": _doc_id(namespace, key)}, {"$set": fields}, upsert=True,
        ))
    if not operations:
        return
    await _guard(
        "set_many",
        _collection("cache_entries").bulk_write(operations, ordered=False),
    )


async def delete(namespace: str, key: str) -> None:
    if not enabled():
        return
    await _guard(
        "delete",
        _collection("cache_entries").delete_one({"_id": _doc_id(namespace, key)}),
    )


async def scan_tag(namespace: str, tag: str, limit: int = 500) -> list:
    """Live values written under *tag*, newest expiry first.

    Warming an in-process *index* — the semantic cache's bucket of query
    embeddings — is the one access pattern a key lookup cannot serve: the
    lookup is a similarity scan, so the whole bucket has to be in memory.
    """
    if not enabled() or not tag:
        return []
    cursor = (
        _collection("cache_entries")
        .find({"ns": namespace, "tag": tag}, {"v": 1, "exp": 1})
        .sort("exp", -1)
        .limit(limit)
    )
    docs = await _guard("scan_tag", cursor.to_list(length=limit), default=[])
    out = []
    for doc in docs or []:
        if _is_expired(doc):
            continue
        value = _decode(doc.get("v"))
        if value is not None:
            out.append(value)
    return out


# ─── Embeddings, kept forever (1.3) ────────────────────────────────────────────
#
# Keys are ``core.paper_identity`` strings ("doi:10.…", "arxiv:2301.…",
# "title:…") qualified by the embedding model and task type, so changing either
# does not serve vectors from the old model against the new one.

def embedding_key(identity: str, model: str, task_type: str) -> str:
    return f"{model}|{task_type}|{identity}"


async def get_embeddings(keys) -> dict:
    """``{key: vector}`` for whichever of *keys* have been embedded before."""
    keys = list(keys or [])
    if not enabled() or not keys:
        return {}
    cursor = _collection("paper_embeddings").find({"_id": {"$in": keys}}, {"e": 1})
    docs = await _guard("get_embeddings", cursor.to_list(length=len(keys)), default=[])
    out = {}
    for doc in docs or []:
        vector = doc.get("e")
        if isinstance(vector, list) and vector:
            out[doc["_id"]] = vector
    return out


async def put_embeddings(items: dict) -> None:
    """Persist ``{key: vector}``. Vectors go in as BSON doubles, not JSON — a
    3072-float array is ~24KB stored and ~60KB as text."""
    if not enabled() or not items:
        return
    from pymongo import UpdateOne

    now = datetime.now(timezone.utc)
    operations = [
        UpdateOne({"_id": key}, {"$set": {"e": list(vector), "u": now}}, upsert=True)
        for key, vector in items.items()
        if isinstance(vector, list) and vector
    ]
    if not operations:
        return
    await _guard(
        "put_embeddings",
        _collection("paper_embeddings").bulk_write(operations, ordered=False),
    )


def stats() -> dict:
    return {
        "enabled": _enabled,
        "available": enabled(),
        "hits": _hits,
        "misses": _misses,
        "errors": _errors,
        "consecutive_failures": _consecutive_failures,
        "cooldown_remaining": max(0.0, round(_skip_until - time.time(), 1)),
    }
