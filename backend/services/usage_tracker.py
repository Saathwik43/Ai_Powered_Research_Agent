"""The daily token wallet: what a user has spent today, on what, and whether
they may spend more.

Two units live here and must not be confused.

* **Tokens** are the real unit. The quota is a token ceiling, enforcement sums
  real provider `usage`, and both the user card and the admin tables count
  tokens. This is the only unit anything may make a decision on.
* **`messages_left`** is a legacy display fiction — `remaining / 5000` — from
  when the sidebar pretended this was a chatbot. It is still returned so a
  cached client build does not break on a missing field; nothing should read it.
"""

import contextvars
import functools
import inspect
from datetime import datetime, timedelta, timezone
import logging
from core.database import db
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Context variable to hold the current user ID globally for the request
current_user_id = contextvars.ContextVar('current_user_id', default=None)

# Which feature is spending, for the same request. Set once at the feature
# boundary (see `query_type_scope`); every LLM call made underneath inherits it,
# so a search that fans out into fifty briefings tags all fifty.
current_query_type = contextvars.ContextVar('current_query_type', default="general")

# Configuration
DAILY_TOKEN_QUOTA = 250_000
TOKENS_PER_MESSAGE = 5_000  # Legacy display unit only — see the module docstring.

# The buckets the wallet splits a day's spend into. Anything a call does not
# tag — the `general` default, venue recommendations, a `query_type` written by
# an older build — lands in `other` rather than being dropped, so the segments
# always sum to `used` and the bar can never under-report.
FEATURE_BUCKETS = ("literature", "pdf_analysis", "manuscript", "other")


class query_type_scope:
    """Tag every billed call made inside this block with *name*.

    **The outermost scope wins.** Manuscript generation extracts evidence from
    papers, and that spend belongs to `manuscript` — it is what the user asked
    for — even though the same extraction code runs under `literature` when the
    survey page drives it. An inner scope therefore never overwrites an outer
    one; it only fills in the `general` default.
    """

    def __init__(self, name: str):
        self.name = name
        self._token = None

    def __enter__(self):
        if current_query_type.get() == "general" and self.name:
            self._token = current_query_type.set(self.name)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            current_query_type.reset(self._token)
        return False


def tagged(name: str):
    """Mark an async entry point as the boundary of feature *name*.

    A decorator rather than a `with` inside each body, because the bodies are
    long and re-indenting them to add a tag would bury the change. It also
    holds for the shapes that outlive their caller: an async generator keeps
    the tag for the whole of its iteration, which is what a streamed manuscript
    section needs — the endpoint has already returned by the time a token is
    billed.
    """
    def decorate(fn):
        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def as_generator(*args, **kwargs):
                with query_type_scope(name):
                    async for item in fn(*args, **kwargs):
                        yield item
            return as_generator

        @functools.wraps(fn)
        async def as_coroutine(*args, **kwargs):
            with query_type_scope(name):
                return await fn(*args, **kwargs)
        return as_coroutine
    return decorate


async def _effective_quota(user_id: str) -> int:
    """This user's daily token ceiling: their `custom_quota`, or the default.

    Never raises. A lookup failure falls back to the default rather than
    denying service or handing out an unbounded allowance.
    """
    try:
        from bson import ObjectId
        user_doc = await db["users"].find_one({"_id": ObjectId(user_id)})
    except Exception:
        return DAILY_TOKEN_QUOTA
    raw = user_doc.get("custom_quota") if user_doc else None
    if raw is None:
        return DAILY_TOKEN_QUOTA
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DAILY_TOKEN_QUOTA


async def _usage_by_feature(user_id: str, today: str) -> dict:
    """Today's tokens for *user_id*, grouped into `FEATURE_BUCKETS`.

    One aggregation serves both the total and the split. The sidebar needs them
    together on every poll and they have to agree — summing the buckets is the
    only way `used` is derived, so a segment can never disagree with the bar.
    """
    pipeline = [
        {"$match": {"user_id": user_id, "date": today}},
        {"$group": {"_id": "$query_type", "total_tokens": {"$sum": "$tokens"}}},
    ]
    cursor = db["usage_logs"].aggregate(pipeline)
    rows = await cursor.to_list(length=None)

    buckets = {name: 0 for name in FEATURE_BUCKETS}
    for row in rows:
        name = row.get("_id")
        buckets[name if name in buckets else "other"] += int(row.get("total_tokens") or 0)
    return buckets


async def check_quota(user_id: str):
    """Raise 429 once today's spend has reached the ceiling."""
    if not user_id:
        return

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    effective_quota = await _effective_quota(user_id)
    total_used = sum((await _usage_by_feature(user_id, today)).values())

    if total_used >= effective_quota:
        raise HTTPException(
            status_code=429,
            detail="Daily message quota exceeded. Please try again tomorrow or contact admin."
        )

async def log_usage(user_id: str, tokens: int, model: str, query_type: str = None):
    """Log token usage to the database.

    *query_type* defaults to whatever feature boundary this call is running
    under, so the four provider call sites do not have to thread a tag through
    every layer between the router and the completion.
    """
    if not user_id or not tokens:
        return

    if query_type is None:
        query_type = current_query_type.get() or "general"

    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        collection = db["usage_logs"]
        await collection.insert_one({
            "user_id": user_id,
            "date": today,
            "tokens": tokens,
            "model": model,
            "query_type": query_type,
            "timestamp": datetime.now(timezone.utc)
        })
    except Exception as e:
        logger.error(f"Failed to log token usage: {e}")

RPD_WARN_THRESHOLD = {"OpenAI": 50}

async def check_provider_rpd(provider: str):
    """Warn when a provider is approaching its daily request cap.  """
    if provider not in RPD_WARN_THRESHOLD:
        return
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    collection = db["usage_logs"]
    count = await collection.count_documents({"model": provider, "date":today})
    limit = RPD_WARN_THRESHOLD[provider]
    if count >= limit * 0.8 :
        logger.warning(f" {provider} at {count}/{limit} RPD ({round(count/limit*100)}%) - approaching daily cap. ")


async def get_user_usage(user_id: str) -> dict:
    """The sidebar wallet: today's spend, what it went on, and time to reset."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    effective_quota = await _effective_quota(user_id)
    by_feature = await _usage_by_feature(user_id, today)
    total_used = sum(by_feature.values())

    remaining = max(0, effective_quota - total_used)
    # A `custom_quota` of 0 is a real admin setting (it locks the account out),
    # and dividing by it used to raise rather than render as a full bar.
    pct_used = min(100.0, total_used / effective_quota * 100) if effective_quota > 0 else 100.0

    # Time until midnight UTC — the quota's own day boundary, not the browser's.
    now = datetime.now(timezone.utc)
    tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    diff = tomorrow - now
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60

    return {
        "quota": effective_quota,
        "used": total_used,
        "remaining": remaining,
        "pct_used": round(pct_used, 1),
        "by_feature": by_feature,
        "reset_in": f"{hours}h {minutes}m",
        # Deprecated, kept one release so an already-loaded client build does
        # not render `undefined`. Nothing should start reading it.
        "messages_left": round(remaining / TOKENS_PER_MESSAGE, 1),
    }
