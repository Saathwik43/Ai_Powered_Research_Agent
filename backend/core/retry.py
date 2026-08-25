"""
One retry policy (P1 API self-heal).

Retries were ad hoc: some integrations had none, some had a bare loop with a
fixed sleep, and none of them read `Retry-After`. A fixed sleep is the worst of
both — it ignores what the server just told us to do, and when several sources
rate-limit at once every retry lands in the same instant, which is the thundering
herd the backoff was supposed to prevent.

The policy here:

* **Retry only what a retry can fix.** A timeout, a connection error, a 429 or a
  5xx. Not a 400, not a 401, not a 404 — those return the same answer however
  many times they are asked, and retrying them turns one wasted call into three.
* **Honour `Retry-After` when the server sends one.** It knows when it will
  serve us; guessing is strictly worse.
* **Full jitter otherwise.** `random.uniform(0, base * 2**attempt)`, capped.
  Equal jitter and no jitter both leave retries clustered.
* **A ceiling on total delay**, because a search has a latency budget and a
  source that needs 30 seconds of backoff has already lost its slot.
"""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.4
DEFAULT_MAX_DELAY = 8.0

# Status codes where trying again can plausibly produce a different answer.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


def _status_of(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) if response is not None else None


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    status = _status_of(exc)
    return status in RETRYABLE_STATUS if status is not None else False


def retry_after_seconds(exc: Exception) -> float | None:
    """The server's own instruction, if it gave one.

    `Retry-After` is either a delay in seconds or an HTTP date. Only the numeric
    form is honoured: the date form needs a trustworthy clock on both ends, and
    a skewed one would produce a wait of hours.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = response.headers.get("retry-after") if getattr(response, "headers", None) else None
    if not raw:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 120 else None


def backoff_delay(attempt: int, base: float, ceiling: float) -> float:
    """Full jitter: uniform over the whole window, not the top of it."""
    window = min(ceiling, base * (2 ** attempt))
    return random.uniform(0, window)


async def with_retry(
    operation,
    *,
    name: str = "",
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    budget: float | None = None,
):
    """Call the async *operation*, retrying what a retry can fix.

    *budget* caps the total time spent sleeping between attempts; once it is
    spent the last exception is raised rather than waiting past the caller's
    own deadline. Anything not retryable is raised on the first attempt.
    """
    slept = 0.0
    last: Exception | None = None

    for attempt in range(max(1, attempts)):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last = exc
            if attempt == attempts - 1 or not is_retryable(exc):
                raise

            delay = retry_after_seconds(exc)
            if delay is None:
                delay = backoff_delay(attempt, base_delay, max_delay)
            if budget is not None and slept + delay > budget:
                logger.info(
                    "Retry budget for %s exhausted after %d attempt(s); giving up.",
                    name or "call", attempt + 1,
                )
                raise
            logger.info(
                "%s failed (%s); retrying in %.2fs (attempt %d/%d).",
                name or "call", type(exc).__name__, delay, attempt + 2, attempts,
            )
            slept += delay
            await asyncio.sleep(delay)

    # Unreachable: the loop either returns or raises.
    raise last  # pragma: no cover
