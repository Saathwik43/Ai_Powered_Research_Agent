"""In-process per-API telemetry: in-flight (during) and last-call (after).

Synthetic probes in admin_status.py cover the "before" question — is this
provider ready? This module covers live traffic from the app itself.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

_LOCK = threading.Lock()
_STATE: dict[str, dict[str, Any]] = {}
_EVENTS: list[dict[str, Any]] = []
_EVENTS_MAX = 200

# Map integration short names onto the labels shown in the admin desk.
_ALIASES = {
    "Gemini": "Google Gemini",
    "Google Gemini": "Google Gemini",
    "HuggingFace": "Hugging Face Inference",
    "Hugging Face Inference": "Hugging Face Inference",
    "PubMed": "PubMed / NCBI",
    "PubMed / NCBI": "PubMed / NCBI",
    "Springer": "Springer Nature",
    "Springer Nature": "Springer Nature",
    "EuropePMC": "Europe PMC",
    "Europe PMC": "Europe PMC",
    "SemanticScholar": "Semantic Scholar",
    "Semantic Scholar": "Semantic Scholar",
    "NVIDIA": "NVIDIA NIM",
    "NVIDIA NIM": "NVIDIA NIM",
    "LlamaParse": "LlamaCloud",
    "LlamaCloud": "LlamaCloud",
    "GitHub": "GitHub Knowledge Repos",
    "GitHub Knowledge Repos": "GitHub Knowledge Repos",
    "Brevo": "Brevo Email",
    "Brevo Email": "Brevo Email",
}


def canonical_name(name: str) -> str:
    return _ALIASES.get(name, name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blank(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "in_flight": 0,
        "calls_ok": 0,
        "calls_fail": 0,
        "last_ok": None,
        "last_latency_ms": None,
        "last_http_status": None,
        "last_error": None,
        "last_items": None,
        "last_operation": None,
        "last_started_at": None,
        "last_finished_at": None,
    }


def _clip(text: Optional[str], limit: int = 180) -> Optional[str]:
    if not text:
        return None
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _entry(name: str) -> dict[str, Any]:
    key = canonical_name(name)
    if key not in _STATE:
        _STATE[key] = _blank(key)
    return _STATE[key]


def _begin(name: str, operation: str) -> None:
    with _LOCK:
        row = _entry(name)
        row["in_flight"] = int(row["in_flight"]) + 1
        row["last_started_at"] = _now_iso()
        row["last_operation"] = operation


def _finish(
    name: str,
    *,
    ok: bool,
    latency_ms: int,
    http_status: Optional[int],
    items: Optional[int],
    error: Optional[str],
    operation: str,
) -> None:
    with _LOCK:
        row = _entry(name)
        row["in_flight"] = max(0, int(row["in_flight"]) - 1)
        if ok:
            row["calls_ok"] = int(row["calls_ok"]) + 1
        else:
            row["calls_fail"] = int(row["calls_fail"]) + 1
        row["last_ok"] = bool(ok)
        row["last_latency_ms"] = latency_ms
        row["last_http_status"] = http_status
        row["last_items"] = items
        row["last_error"] = _clip(error)
        row["last_operation"] = operation
        finished_at = _now_iso()
        row["last_finished_at"] = finished_at
        _EVENTS.append({
            "ts": finished_at,
            "name": canonical_name(name),
            "operation": operation,
            "ok": bool(ok),
            "latency_ms": latency_ms,
            "http_status": http_status,
            "items": items,
            "error": _clip(error),
        })
        if len(_EVENTS) > _EVENTS_MAX:
            del _EVENTS[: len(_EVENTS) - _EVENTS_MAX]


def _abandon(name: str) -> None:
    with _LOCK:
        row = _entry(name)
        row["in_flight"] = max(0, int(row["in_flight"]) - 1)


class CallTracker:
    def __init__(self, name: str, operation: str = "request"):
        self.name = canonical_name(name)
        self.operation = operation
        self._t0 = time.monotonic()
        self.finished = False
        _begin(self.name, self.operation)

    def succeed(self, *, http_status: Optional[int] = None, items: Optional[int] = None) -> None:
        self._done(True, http_status=http_status, items=items, error=None)

    def fail(
        self,
        *,
        http_status: Optional[int] = None,
        error: Optional[str] = None,
        items: Optional[int] = None,
    ) -> None:
        self._done(False, http_status=http_status, items=items, error=error)

    def _done(self, ok: bool, *, http_status, items, error) -> None:
        if self.finished:
            return
        self.finished = True
        _finish(
            self.name,
            ok=ok,
            latency_ms=round((time.monotonic() - self._t0) * 1000),
            http_status=http_status,
            items=items,
            error=error,
            operation=self.operation,
        )

    def close(self) -> None:
        if not self.finished:
            _abandon(self.name)
            self.finished = True


@asynccontextmanager
async def track_call(name: str, operation: str = "request"):
    rec = CallTracker(name, operation)
    try:
        yield rec
    except Exception as exc:
        rec.fail(error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        rec.close()


@contextmanager
def track_call_sync(name: str, operation: str = "request"):
    rec = CallTracker(name, operation)
    try:
        yield rec
    except Exception as exc:
        rec.fail(error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        rec.close()


def snapshot(name: Optional[str] = None) -> dict[str, Any]:
    with _LOCK:
        if name:
            row = _STATE.get(canonical_name(name))
            return dict(row) if row else _blank(canonical_name(name))
        return {k: dict(v) for k, v in _STATE.items()}


def live_for(name: str) -> dict[str, Any]:
    row = snapshot(name)
    ok = int(row.get("calls_ok") or 0)
    fail = int(row.get("calls_fail") or 0)
    total = ok + fail
    row["success_pct"] = round(100.0 * ok / total, 1) if total else None
    return row


def inflight_total() -> int:
    with _LOCK:
        return sum(int(v.get("in_flight") or 0) for v in _STATE.values())


def recent_events(
    limit: int = 80,
    *,
    name: Optional[str] = None,
    ok: Optional[bool] = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = list(_EVENTS)
    if name:
        want = canonical_name(name)
        rows = [e for e in rows if e.get("name") == want]
    if ok is not None:
        rows = [e for e in rows if bool(e.get("ok")) is bool(ok)]
    rows.reverse()
    return rows[: max(1, min(int(limit or 80), _EVENTS_MAX))]
