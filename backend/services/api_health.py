"""
Rolling health and a circuit breaker for the external APIs (P1 API self-heal).

`api_telemetry` already records every call, but it records *totals*: `calls_ok`
and `calls_fail` since the process started. A source that answered a thousand
times this morning and has failed every call for the last ten minutes still
reads as 99% healthy, so nothing in the system could act on it — and nothing
did. Every search paid the full per-source timeout for a database that was
already known to be down, and the admin desk's "which sources failed" line was
a fact about the past rather than about now.

Two pieces:

**Rolling window.** Outcomes inside `WINDOW_SECONDS`, so "failing right now" is
answerable. Bounded per source; this is a health signal, not an audit log —
`api_telemetry.recent_events` is the audit log.

**Circuit breaker.** When a source fails enough, in a high enough proportion,
inside the window, stop calling it for a cooldown. The point is not to protect
the source: it is that a dead source costs the *caller* its full timeout on
every search, and eight sources' worth of that is the difference between a
5-second search and a 20-second one. After the cooldown one probe call is let
through (half-open); it decides whether the breaker closes or the cooldown
doubles.

Fed from `api_telemetry._finish`, so every existing `track_call` site
contributes with no change at the call site.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

# How far back "right now" reaches.
WINDOW_SECONDS = 300.0

# Enough calls to distinguish an outage from one unlucky request. Below this the
# breaker never opens, whatever the failure rate.
MIN_CALLS = 5

# Proportion of the judged calls that must have failed.
FAILURE_RATE = 0.6

# How many of the window's most recent calls the decision is made on.
#
# Judging the *whole* window sounds more principled and behaves badly: a source
# that answered sixty times this morning and has failed every call since needs
# ninety more failures before the arithmetic notices, which is exactly the delay
# the rolling window was introduced to remove. The window bounds how far back a
# call counts at all; this bounds how many of them get a vote.
EVAL_SAMPLE = 20

# First cooldown, and the ceiling it doubles towards.
BASE_COOLDOWN = 60.0
MAX_COOLDOWN = 900.0

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

_LOCK = threading.Lock()
# name -> deque[(timestamp, ok)]
_WINDOW: dict[str, deque] = {}
# name -> {"state", "opened_at", "cooldown", "trips", "probe_in_flight"}
_BREAKERS: dict[str, dict] = {}

_enabled = True


def set_enabled(value: bool) -> None:
    """Switch the breaker off (the test suite does; a search must be
    deterministic regardless of what earlier tests recorded)."""
    global _enabled
    _enabled = bool(value)
    if not _enabled:
        reset()


def reset(name: str | None = None) -> None:
    with _LOCK:
        if name is None:
            _WINDOW.clear()
            _BREAKERS.clear()
        else:
            _WINDOW.pop(name, None)
            _BREAKERS.pop(name, None)


def _trim(window: deque, now: float) -> None:
    while window and (now - window[0][0]) > WINDOW_SECONDS:
        window.popleft()


def _breaker(name: str) -> dict:
    if name not in _BREAKERS:
        _BREAKERS[name] = {
            "state": STATE_CLOSED, "opened_at": 0.0,
            "cooldown": BASE_COOLDOWN, "trips": 0, "probe_in_flight": False,
        }
    return _BREAKERS[name]


def record(name: str, ok: bool) -> None:
    """Note one call outcome. Never raises — it is on every API call path."""
    if not name:
        return
    now = time.time()
    try:
        with _LOCK:
            window = _WINDOW.setdefault(name, deque(maxlen=200))
            window.append((now, bool(ok)))
            _trim(window, now)
            breaker = _breaker(name)

            if ok:
                if breaker["state"] in (STATE_HALF_OPEN, STATE_OPEN):
                    logger.info("Circuit for %s closed after a successful call.", name)
                breaker.update({
                    "state": STATE_CLOSED, "cooldown": BASE_COOLDOWN,
                    "probe_in_flight": False,
                })
                return

            breaker["probe_in_flight"] = False
            if breaker["state"] == STATE_HALF_OPEN:
                # The probe failed: straight back to open, waiting longer.
                breaker["cooldown"] = min(MAX_COOLDOWN, breaker["cooldown"] * 2)
                breaker.update({"state": STATE_OPEN, "opened_at": now})
                breaker["trips"] += 1
                logger.warning(
                    "Circuit for %s re-opened for %.0fs (probe failed).", name, breaker["cooldown"]
                )
                return

            if breaker["state"] == STATE_OPEN:
                return

            recent = list(window)[-EVAL_SAMPLE:]
            failures = sum(1 for _ts, was_ok in recent if not was_ok)
            total = len(recent)
            if total >= MIN_CALLS and (failures / total) >= FAILURE_RATE:
                breaker.update({"state": STATE_OPEN, "opened_at": now})
                breaker["trips"] += 1
                logger.warning(
                    "Circuit for %s opened for %.0fs (%d/%d failures in the last %.0fs).",
                    name, breaker["cooldown"], failures, total, WINDOW_SECONDS,
                )
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("Health record failed for %s: %s", name, e)


def allow(name: str) -> bool:
    """Whether a call to *name* should be attempted now.

    False only while a breaker is open and its cooldown has not elapsed. The
    first caller after the cooldown gets the half-open probe; everyone else in
    that moment is still refused, so a recovering source is tested by one
    request rather than hit by the whole fan-out.
    """
    if not _enabled or not name:
        return True
    now = time.time()
    with _LOCK:
        breaker = _BREAKERS.get(name)
        if not breaker or breaker["state"] == STATE_CLOSED:
            return True
        if breaker["state"] == STATE_HALF_OPEN:
            if breaker["probe_in_flight"]:
                return False
            breaker["probe_in_flight"] = True
            return True
        if (now - breaker["opened_at"]) >= breaker["cooldown"]:
            breaker.update({"state": STATE_HALF_OPEN, "probe_in_flight": True})
            logger.info("Circuit for %s half-open: letting one probe through.", name)
            return True
        return False


def blocked_reason(name: str) -> str | None:
    """A short explanation for a skipped source, for the per-database yield."""
    with _LOCK:
        breaker = _BREAKERS.get(name)
        if not breaker or breaker["state"] == STATE_CLOSED:
            return None
        remaining = max(0.0, breaker["cooldown"] - (time.time() - breaker["opened_at"]))
        return f"circuit open, retrying in {remaining:.0f}s"


def health(name: str) -> dict:
    now = time.time()
    with _LOCK:
        window = _WINDOW.get(name)
        breaker = dict(_BREAKERS.get(name) or _breaker(name))
        if window:
            _trim(window, now)
            window_calls = len(window)
            # Reported on the same sample the breaker decides on, so the admin
            # desk and the breaker can never disagree about a source.
            recent = list(window)[-EVAL_SAMPLE:]
            total = len(recent)
            failures = sum(1 for _ts, ok in recent if not ok)
        else:
            window_calls = total = failures = 0
    return {
        "name": name,
        "window_seconds": WINDOW_SECONDS,
        "window_calls": window_calls,
        "calls": total,
        "failures": failures,
        "failure_rate": round(failures / total, 3) if total else None,
        "state": breaker["state"],
        "cooldown": breaker["cooldown"],
        "trips": breaker["trips"],
        "retry_in": (
            max(0.0, round(breaker["cooldown"] - (now - breaker["opened_at"]), 1))
            if breaker["state"] == STATE_OPEN else 0.0
        ),
    }


def snapshot() -> dict:
    with _LOCK:
        names = set(_WINDOW) | set(_BREAKERS)
    return {name: health(name) for name in sorted(names)}


def open_circuits() -> set[str]:
    """Names currently refusing calls — what the fan-out skips."""
    with _LOCK:
        return {
            name for name, breaker in _BREAKERS.items()
            if breaker["state"] == STATE_OPEN
            and (time.time() - breaker["opened_at"]) < breaker["cooldown"]
        }
