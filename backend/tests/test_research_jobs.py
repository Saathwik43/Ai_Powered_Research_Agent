"""
1.5 — research runs in the background and says what it is doing.

Two claims are worth pinning. That the pipeline reports each stage *while it is
running*, not in a batch once the wait everybody minded is already over — which
is why the stream test asserts a status frame arrives before preparation
finishes. And that two callers asking for the same topic join one run: the
corpus is public literature, so a second pipeline would be a second bill for
identical work.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from ai import manuscript_generation
from core.query_key import canonical_key
from services import research_jobs

TOPIC = "graph neural networks for drug discovery"
JOB_KEY = canonical_key(TOPIC)


@pytest.fixture
def anyio_backend():
    """asyncio only. The queue that carries stage updates out of the generator,
    and the task the job runs on, are asyncio primitives — exercising them under
    trio would test nothing this code claims to support."""
    return "asyncio"

PAPERS = [
    {"title": "GNNs for molecules", "authors": "A", "year": "2024",
     "abstract": "Graphs.", "doi": "10.1/a", "source": "arXiv"},
    {"title": "Molecular property prediction", "authors": "B", "year": "2023",
     "abstract": "Prediction.", "doi": "10.1/b", "source": "OpenAlex"},
]


@pytest.fixture(autouse=True)
def _clean_caches():
    manuscript_generation._research_cache.clear()
    research_jobs._running.clear()
    yield
    manuscript_generation._research_cache.clear()
    research_jobs._running.clear()


def _stub_pipeline():
    """search → screen → evidence, all stubbed, so only the reporting is under test."""
    return (
        patch.object(manuscript_generation, "search_all", AsyncMock(return_value=[dict(p) for p in PAPERS])),
        patch.object(manuscript_generation, "_filter_relevant_papers",
                     AsyncMock(side_effect=lambda topic, papers: papers)),
        patch.object(manuscript_generation, "extract_evidence_for_paper",
                     AsyncMock(return_value=({"method": "m"}, "abstract"))),
    )


# ─── Stage reporting ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_every_stage_is_reported_in_order():
    seen = []
    patches = _stub_pipeline()
    for p in patches:
        p.start()
    try:
        papers = await manuscript_generation.prepare_corpus(TOPIC, progress=seen.append)
    finally:
        for p in patches:
            p.stop()

    assert len(papers) == 2
    stages = [u["stage"] for u in seen]
    assert stages == ["search", "search", "screen", "screen", "evidence", "evidence", "ready"]
    assert seen[-1]["papers"] == 2


@pytest.mark.anyio
async def test_a_prepared_corpus_skips_the_pipeline_entirely():
    """The point of the background job: pressing Generate afterwards must not
    re-run the fan-out."""
    seen = []
    patches = _stub_pipeline()
    for p in patches:
        p.start()
    try:
        await manuscript_generation.prepare_corpus(TOPIC)
        search = patches[0].new  # the AsyncMock standing in for search_all
        before = search.await_count
        await manuscript_generation.prepare_corpus(TOPIC, progress=seen.append)
        assert search.await_count == before
    finally:
        for p in patches:
            p.stop()

    assert [u["stage"] for u in seen] == ["ready"]
    assert manuscript_generation.corpus_is_ready(TOPIC) is True


@pytest.mark.anyio
async def test_an_empty_search_does_not_claim_papers_are_ready():
    seen = []
    with patch.object(manuscript_generation, "search_all", AsyncMock(return_value=[])):
        papers = await manuscript_generation.prepare_corpus(TOPIC, progress=seen.append)

    assert papers == []
    skipped = [u for u in seen if u["status"] == "skipped"]
    assert {u["stage"] for u in skipped} == {"screen", "evidence"}
    assert seen[-1] == {"stage": "ready", "status": "done", "detail": "0 paper(s) ready.", "papers": 0}


# ─── Progress reaches the browser while the work is happening ──────────────────

@pytest.mark.anyio
async def test_status_frames_arrive_before_preparation_finishes():
    """Buffering the stages and flushing them at the end would satisfy a naive
    assertion and help nobody — the frames have to arrive during the wait."""
    released = asyncio.Event()

    async def slow_prepare(topic, section, context, citation_style, provider=None,
                           model=None, progress=None):
        progress({"stage": "search", "status": "running", "detail": "Searching…"})
        await released.wait()
        return manuscript_generation._Prepared(
            user_prompt="p", system_prompt="s", references_mapping={},
            gap_analysis_data=None, papers=[], error=None, cached_content=None,
        )

    with patch.object(manuscript_generation, "_prepare_generation", slow_prepare), \
         patch.object(manuscript_generation, "stream_completion",
                      lambda *a, **k: _empty_stream()):
        stream = manuscript_generation.generate_section_stream(
            TOPIC, "abstract", "", "ieee"
        )
        first = await asyncio.wait_for(anext(stream), timeout=2.0)
        assert first == {"type": "status", "stage": "search", "status": "running",
                         "detail": "Searching…"}
        released.set()
        rest = [frame async for frame in stream]

    assert any(f.get("type") == "sources_list" for f in rest)


async def _empty_stream():
    yield {"type": "done"}


# ─── The job document ──────────────────────────────────────────────────────────

class _FakeJobs:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query, projection=None):
        doc = self.docs.get(query.get("_id"))
        return dict(doc) if doc else None

    async def update_one(self, query, update, upsert=False):
        key = query.get("_id")
        doc = self.docs.get(key)
        if doc is None:
            if not upsert:
                return None
            doc = {"_id": key}
            self.docs[key] = doc
        doc.update(update.get("$set") or {})
        for field, value in (update.get("$push") or {}).items():
            doc.setdefault(field, []).append(value)
        return None


@pytest.mark.anyio
async def test_two_callers_for_one_topic_share_a_single_run():
    jobs = _FakeJobs()
    started = 0

    async def counting_prepare(topic, progress=None, force=False):
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return list(PAPERS)

    with patch.object(research_jobs, "db", {research_jobs.COLLECTION: jobs}), \
         patch.object(manuscript_generation, "prepare_corpus", counting_prepare):
        first, second = await asyncio.gather(
            research_jobs.ensure_job(TOPIC),
            research_jobs.ensure_job(TOPIC.upper()),  # same canonical topic
        )
        assert first["id"] == second["id"]
        await asyncio.gather(*list(research_jobs._running.values()))

    assert started == 1
    assert jobs.docs[first["id"]]["status"] == research_jobs.STATUS_READY
    assert jobs.docs[first["id"]]["papers"] == 2


@pytest.mark.anyio
async def test_a_failed_run_is_recorded_not_left_spinning():
    jobs = _FakeJobs()

    async def broken(topic, progress=None, force=False):
        raise RuntimeError("every source timed out")

    with patch.object(research_jobs, "db", {research_jobs.COLLECTION: jobs}), \
         patch.object(manuscript_generation, "prepare_corpus", broken):
        job = await research_jobs.ensure_job(TOPIC)
        await asyncio.gather(*list(research_jobs._running.values()))

    stored = jobs.docs[job["id"]]
    assert stored["status"] == research_jobs.STATUS_FAILED
    assert "every source timed out" in stored["error"]


@pytest.mark.anyio
async def test_watch_replays_what_already_happened_then_ends():
    """A client that reconnects mid-run sees the stages it missed — which is why
    they live on the document rather than in a queue in one worker."""
    jobs = _FakeJobs()
    jobs.docs[JOB_KEY] = {
        "_id": JOB_KEY,
        "status": research_jobs.STATUS_READY,
        "papers": 2,
        "stages": [
            {"stage": "search", "status": "done", "detail": "12 candidates."},
            {"stage": "screen", "status": "done", "detail": "2 kept."},
        ],
        "updated_at": datetime.now(timezone.utc),
    }

    with patch.object(research_jobs, "db", {research_jobs.COLLECTION: jobs}):
        frames = [f async for f in research_jobs.watch(TOPIC, poll_seconds=0.01)]

    assert [f.get("stage") for f in frames[:2]] == ["search", "screen"]
    assert frames[-1] == {"type": "research_done", "papers": 2}


@pytest.mark.anyio
async def test_a_run_abandoned_by_a_redeploy_can_be_taken_over():
    jobs = _FakeJobs()
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=research_jobs.STALE_AFTER_SECONDS + 60
    )
    key = JOB_KEY
    jobs.docs[key] = {
        "_id": key, "status": research_jobs.STATUS_RUNNING, "stages": [],
        "updated_at": stale,
    }

    async def quick(topic, progress=None, force=False):
        return list(PAPERS)

    with patch.object(research_jobs, "db", {research_jobs.COLLECTION: jobs}), \
         patch.object(manuscript_generation, "prepare_corpus", quick):
        await research_jobs.ensure_job(TOPIC)
        await asyncio.gather(*list(research_jobs._running.values()))

    assert jobs.docs[key]["status"] == research_jobs.STATUS_READY
