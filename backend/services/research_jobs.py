"""
Background research jobs (1.5).

Generating a section on a cold topic runs the whole research pipeline first —
an 8-source fan-out, a relevance-classifier pass, then up to fifteen full-text
fetches. That is tens of seconds during which the user has pressed Generate and
is looking at nothing. Two separate problems live in that sentence:

  1. **Nothing is shown.** Fixed by reporting stages: `prepare_corpus` takes a
     progress callback and `generate_section_stream` forwards each stage to the
     browser as it happens.

  2. **It is on the critical path at all.** Fixed here. The corpus for a topic
     does not depend on which section is being written, so it can be built the
     moment the topic is known — while the user is still choosing a section or
     typing context. By the time Generate is pressed the pipeline is a cache
     read and the first token is the first thing that has to happen.

Progress is written to a Mongo document rather than held in memory, because the
client that reconnects (or the second tab, or the worker that did not start the
job) has to be able to see it. `RP-11` asks for exactly this shape for the
research runs it describes — land it once.

A job is keyed by canonical topic, not by user: the corpus is public literature,
the same work for everyone, and two users researching the same topic should
join one job rather than run two.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from core.database import db
from core.query_key import canonical_key

logger = logging.getLogger(__name__)

COLLECTION = "research_jobs"

STATUS_RUNNING = "running"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# A job that has not written a stage update in this long is presumed dead — the
# worker running it was redeployed mid-pipeline — and a new one may take over.
STALE_AFTER_SECONDS = 180

# How long a finished job's record is worth keeping. Matches the corpus cache
# TTL: past that the "ready" record would be advertising a corpus that has gone.
JOB_TTL_SECONDS = 3600

# Tasks are held so they are not garbage collected mid-flight; asyncio only
# keeps a weak reference to a running task.
_running: dict[str, asyncio.Task] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public(doc: dict | None) -> dict | None:
    if not doc:
        return None
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    return out


def _is_stale(doc: dict) -> bool:
    if doc.get("status") != STATUS_RUNNING:
        return False
    updated = doc.get("updated_at")
    if not isinstance(updated, datetime):
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated < _now() - timedelta(seconds=STALE_AFTER_SECONDS)


async def get_job(topic: str) -> dict | None:
    """The current job for *topic*, or None."""
    try:
        doc = await db[COLLECTION].find_one({"_id": canonical_key(topic)})
    except Exception as e:
        logger.warning("Research job lookup failed for '%s': %s", topic, e)
        return None
    return _public(doc)


async def ensure_job(topic: str) -> dict:
    """Start research for *topic*, or join the run already in progress.

    Idempotent by design: the Generate button, a topic being typed and a draft
    being loaded can all call this, and only one pipeline runs.
    """
    from ai.manuscript_generation import corpus_is_ready

    key = canonical_key(topic)
    existing = await db[COLLECTION].find_one({"_id": key})

    if existing and existing.get("status") == STATUS_READY:
        fresh = existing.get("updated_at")
        if isinstance(fresh, datetime):
            if fresh.tzinfo is None:
                fresh = fresh.replace(tzinfo=timezone.utc)
            if fresh > _now() - timedelta(seconds=JOB_TTL_SECONDS):
                return _public(existing)

    if existing and existing.get("status") == STATUS_RUNNING and not _is_stale(existing):
        return _public(existing)

    if corpus_is_ready(topic):
        # Nothing to do — this worker already holds the corpus.
        doc = _blank_job(key, topic)
        doc.update({"status": STATUS_READY, "stages": [
            {"stage": "ready", "status": "done", "detail": "Research already prepared.",
             "at": _now().isoformat()},
        ]})
        await db[COLLECTION].update_one({"_id": key}, {"$set": doc}, upsert=True)
        return _public(doc)

    doc = _blank_job(key, topic)
    await db[COLLECTION].update_one({"_id": key}, {"$set": doc}, upsert=True)

    task = _running.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_run(key, topic))
        _running[key] = task
        task.add_done_callback(lambda t, k=key: _running.pop(k, None))
    return _public(doc)


def _blank_job(key: str, topic: str) -> dict:
    return {
        "_id": key,
        "topic": topic,
        "status": STATUS_RUNNING,
        "stages": [],
        "papers": 0,
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
        # TTL-indexed so finished jobs do not accumulate.
        "expires_at": _now() + timedelta(seconds=JOB_TTL_SECONDS),
    }


async def _record_stage(key: str, update: dict) -> None:
    entry = {**update, "at": _now().isoformat()}
    try:
        await db[COLLECTION].update_one(
            {"_id": key},
            {
                "$push": {"stages": entry},
                "$set": {
                    "updated_at": _now(),
                    "papers": int(update.get("papers") or 0),
                    "expires_at": _now() + timedelta(seconds=JOB_TTL_SECONDS),
                },
            },
        )
    except Exception as e:
        logger.warning("Could not record research stage for '%s': %s", key, e)


async def _run(key: str, topic: str) -> None:
    """Build the corpus, writing each stage to the job document as it goes."""
    from ai.manuscript_generation import prepare_corpus

    started = time.time()
    try:
        papers = await prepare_corpus(
            topic, progress=lambda update: _record_stage(key, update)
        )
    except asyncio.CancelledError:
        await db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"status": STATUS_FAILED, "error": "cancelled", "updated_at": _now()}},
        )
        raise
    except Exception as e:
        logger.error("Research job for '%s' failed: %s", topic, e)
        await db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"status": STATUS_FAILED, "error": str(e)[:300], "updated_at": _now()}},
        )
        return

    await db[COLLECTION].update_one(
        {"_id": key},
        {"$set": {
            "status": STATUS_READY,
            "papers": len(papers or []),
            "updated_at": _now(),
            "expires_at": _now() + timedelta(seconds=JOB_TTL_SECONDS),
            "took_ms": int((time.time() - started) * 1000),
        }},
    )
    logger.info(
        "Research job for '%s' ready: %d paper(s) in %.1fs",
        topic, len(papers or []), time.time() - started,
    )


async def watch(topic: str, poll_seconds: float = 0.8, timeout_seconds: float = 300.0):
    """Yield stage updates for *topic* until the job finishes.

    Reads the job document rather than an in-process queue, so a client that
    reconnects — or one served by a different worker than the one running the
    pipeline — still sees progress. That is the whole reason the stages are
    persisted.
    """
    seen = 0
    deadline = time.time() + timeout_seconds

    while True:
        job = await get_job(topic)
        if job is None:
            yield {"type": "status", "stage": "ready", "status": "unknown",
                   "detail": "No research run for this topic."}
            return

        stages = job.get("stages") or []
        for entry in stages[seen:]:
            yield {"type": "status", **entry}
        seen = len(stages)

        if job.get("status") == STATUS_READY:
            yield {"type": "research_done", "papers": job.get("papers", 0)}
            return
        if job.get("status") == STATUS_FAILED:
            yield {"type": "research_failed", "error": job.get("error") or "Research failed."}
            return
        if time.time() > deadline:
            yield {"type": "research_failed", "error": "Research is taking longer than expected."}
            return

        await asyncio.sleep(poll_seconds)
