"""The daily token wallet: grouping, the quota ceiling, and feature tagging.

The wallet is the only place a user is told what they have spent, so the two
things that must hold are that the segments sum to `used` (a segment that
disagrees with the bar is worse than no segments) and that `check_quota` still
refuses at the ceiling.
"""

import pytest

from services import usage_tracker as ut

# A real 24-hex id: `_effective_quota` runs it through `ObjectId`.
USER = "6501f2a4b3c4d5e6f7a8b9c0"
TODAY = None  # set per test from the module's own clock


class _FakeUsageLogs:
    """Just enough of the `usage_logs` collection: the group-by-query_type
    aggregation `_usage_by_feature` runs, and `insert_one`."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def aggregate(self, pipeline):
        match = pipeline[0]["$match"]
        group_key = pipeline[1]["$group"]["_id"]
        assert group_key == "$query_type", "the wallet groups by feature"

        totals = {}
        for doc in self.docs:
            if any(doc.get(k) != v for k, v in match.items()):
                continue
            key = doc.get("query_type")
            totals[key] = totals.get(key, 0) + int(doc.get("tokens") or 0)
        rows = [{"_id": k, "total_tokens": v} for k, v in totals.items()]

        class _Cursor:
            async def to_list(self, length=None):
                return rows

        return _Cursor()

    async def insert_one(self, doc):
        self.docs.append(doc)
        return None


class _FakeUsers:
    def __init__(self, doc=None):
        self.doc = doc

    async def find_one(self, _query, *args, **kwargs):
        return self.doc


def _db(logs=None, user_doc=None):
    return {"usage_logs": _FakeUsageLogs(logs), "users": _FakeUsers(user_doc)}


def _today():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _log(tokens, query_type, date=None, user_id=USER):
    return {
        "user_id": user_id,
        "date": date or _today(),
        "tokens": tokens,
        "model": "Gemini",
        "query_type": query_type,
    }


# ─── by_feature grouping ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_usage_is_split_by_feature_and_segments_sum_to_used(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([
        _log(1200, "literature"),
        _log(18000, "pdf_analysis"),
        _log(11000, "manuscript"),
        _log(1000, "general"),
    ]))

    usage = await ut.get_user_usage(USER)

    assert usage["by_feature"] == {
        "literature": 1200,
        "pdf_analysis": 18000,
        "manuscript": 11000,
        "other": 1000,
    }
    assert usage["used"] == 31200
    assert sum(usage["by_feature"].values()) == usage["used"]
    assert usage["quota"] == ut.DAILY_TOKEN_QUOTA
    assert usage["remaining"] == 250_000 - 31_200
    assert usage["pct_used"] == pytest.approx(12.5)


@pytest.mark.anyio
async def test_unknown_and_missing_tags_land_in_other(monkeypatch):
    """`venue`, an untagged log, and a tag written by an older build all have
    to be counted somewhere — dropping them would under-report the day."""
    logs = [
        _log(500, "venue"),
        _log(700, "general"),
        _log(300, "some_future_feature"),
        {"user_id": USER, "date": _today(), "tokens": 400, "model": "Gemini"},  # no tag
    ]
    monkeypatch.setattr(ut, "db", _db(logs))

    usage = await ut.get_user_usage(USER)

    assert usage["by_feature"]["other"] == 1900
    assert usage["by_feature"]["literature"] == 0
    assert usage["used"] == 1900


@pytest.mark.anyio
async def test_only_todays_logs_and_only_this_user(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([
        _log(5000, "literature"),
        _log(9999, "literature", date="1999-01-01"),
        _log(8888, "literature", user_id="6501f2a4b3c4d5e6f7a8b9c1"),
    ]))

    usage = await ut.get_user_usage(USER)

    assert usage["used"] == 5000


@pytest.mark.anyio
async def test_zero_usage_reads_as_zero_percent_and_full_remaining(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([]))

    usage = await ut.get_user_usage(USER)

    assert usage["used"] == 0
    assert usage["pct_used"] == 0
    assert usage["remaining"] == ut.DAILY_TOKEN_QUOTA
    # Present and zeroed, so the client renders an empty bar rather than
    # crashing on a missing key.
    assert usage["by_feature"] == {k: 0 for k in ut.FEATURE_BUCKETS}


# ─── the ceiling ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_custom_quota_replaces_the_default(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([_log(5000, "manuscript")], {"custom_quota": 20_000}))

    usage = await ut.get_user_usage(USER)

    assert usage["quota"] == 20_000
    assert usage["remaining"] == 15_000
    assert usage["pct_used"] == pytest.approx(25.0)


@pytest.mark.anyio
async def test_usage_over_quota_clamps_at_full(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([_log(30_000, "manuscript")], {"custom_quota": 20_000}))

    usage = await ut.get_user_usage(USER)

    assert usage["pct_used"] == 100
    assert usage["remaining"] == 0
    assert usage["messages_left"] == 0


@pytest.mark.anyio
async def test_zero_custom_quota_renders_full_rather_than_dividing_by_zero(monkeypatch):
    """A `custom_quota` of 0 is a real admin setting — it locks the account."""
    monkeypatch.setattr(ut, "db", _db([], {"custom_quota": 0}))

    usage = await ut.get_user_usage(USER)

    assert usage["quota"] == 0
    assert usage["pct_used"] == 100
    assert usage["remaining"] == 0


@pytest.mark.anyio
async def test_check_quota_refuses_at_the_ceiling(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(ut, "db", _db([_log(20_000, "manuscript")], {"custom_quota": 20_000}))
    with pytest.raises(HTTPException) as exc:
        await ut.check_quota(USER)
    assert exc.value.status_code == 429


@pytest.mark.anyio
async def test_check_quota_allows_below_the_ceiling(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([
        _log(9_000, "manuscript"),
        _log(1_000, "literature"),
    ], {"custom_quota": 20_000}))

    await ut.check_quota(USER)  # must not raise


@pytest.mark.anyio
async def test_check_quota_sums_every_feature_not_just_one(monkeypatch):
    """The grouped aggregation replaced a single `$sum` over all rows. If the
    buckets were not summed back together the ceiling would never be reached."""
    from fastapi import HTTPException

    monkeypatch.setattr(ut, "db", _db([
        _log(7_000, "literature"),
        _log(7_000, "pdf_analysis"),
        _log(6_000, "manuscript"),
    ], {"custom_quota": 20_000}))

    with pytest.raises(HTTPException):
        await ut.check_quota(USER)


# ─── legacy field ──────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_messages_left_still_returned_for_older_clients(monkeypatch):
    monkeypatch.setattr(ut, "db", _db([_log(50_000, "literature")]))

    usage = await ut.get_user_usage(USER)

    assert usage["messages_left"] == pytest.approx(200_000 / ut.TOKENS_PER_MESSAGE)


# ─── feature tagging ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_log_usage_without_a_scope_records_general(monkeypatch):
    fake = _db([])
    monkeypatch.setattr(ut, "db", fake)

    await ut.log_usage(USER, 100, "Gemini")

    assert fake["usage_logs"].docs[0]["query_type"] == "general"


@pytest.mark.anyio
async def test_log_usage_inherits_the_surrounding_scope(monkeypatch):
    fake = _db([])
    monkeypatch.setattr(ut, "db", fake)

    with ut.query_type_scope("pdf_analysis"):
        await ut.log_usage(USER, 100, "Gemini")

    assert fake["usage_logs"].docs[0]["query_type"] == "pdf_analysis"


@pytest.mark.anyio
async def test_an_explicit_query_type_still_wins(monkeypatch):
    fake = _db([])
    monkeypatch.setattr(ut, "db", fake)

    with ut.query_type_scope("literature"):
        await ut.log_usage(USER, 100, "Gemini", query_type="manuscript")

    assert fake["usage_logs"].docs[0]["query_type"] == "manuscript"


@pytest.mark.anyio
async def test_the_outermost_scope_wins(monkeypatch):
    """Evidence extraction runs under both surfaces. When manuscript generation
    drives it the spend is the manuscript's, not the literature survey's."""
    fake = _db([])
    monkeypatch.setattr(ut, "db", fake)

    with ut.query_type_scope("manuscript"):
        with ut.query_type_scope("literature"):
            await ut.log_usage(USER, 100, "Gemini")

    assert fake["usage_logs"].docs[0]["query_type"] == "manuscript"


def test_scope_resets_on_exit():
    assert ut.current_query_type.get() == "general"
    with ut.query_type_scope("manuscript"):
        assert ut.current_query_type.get() == "manuscript"
    assert ut.current_query_type.get() == "general"


def test_scope_resets_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with ut.query_type_scope("literature"):
            raise ValueError("boom")
    assert ut.current_query_type.get() == "general"


@pytest.mark.anyio
async def test_grouping_reads_the_tag_log_usage_wrote(monkeypatch):
    """End to end through the fake: tag, log, then read the wallet back."""
    fake = _db([])
    monkeypatch.setattr(ut, "db", fake)

    with ut.query_type_scope("literature"):
        await ut.log_usage(USER, 1_200, "Gemini")
    with ut.query_type_scope("pdf_analysis"):
        await ut.log_usage(USER, 18_000, "Gemini")

    usage = await ut.get_user_usage(USER)

    assert usage["by_feature"]["literature"] == 1_200
    assert usage["by_feature"]["pdf_analysis"] == 18_000
    assert usage["used"] == 19_200


# ─── the tag reaches the billing call, through the real code path ─────────────

# `generate_completion` reaches for `asyncio.wait_for`, so the real provider
# cascade only runs on asyncio. Production is asyncio; exercising it under trio
# would be testing a path that does not exist.
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.anyio
async def test_a_literature_brief_is_billed_as_literature(anyio_backend, monkeypatch):
    """End to end: the decorated entry point, the real `generate_completion`,
    the real `log_usage`. Asserting the contextvar in isolation would not catch
    the tag being lost somewhere between the two."""
    from ai import llm_provider, paper_brief

    paper_brief._brief_cache.clear()
    fake = _db([])
    monkeypatch.setattr(ut, "db", fake)

    async def _fake_groq(system_prompt, user_prompt, max_tokens, temperature):
        return ('{"bullets": ["About: a paper."]}', 1234)

    monkeypatch.setattr(llm_provider, "_generate_groq", _fake_groq)
    token = ut.current_user_id.set(USER)
    try:
        await paper_brief.brief_paper({
            "title": "Adaptive response for intrusion detection",
            "abstract": "We present a system. It works well on three datasets.",
            "doi": "10.1000/tagging-test",
        })
    finally:
        ut.current_user_id.reset(token)

    assert len(fake["usage_logs"].docs) == 1
    logged = fake["usage_logs"].docs[0]
    assert logged["query_type"] == "literature"
    assert logged["tokens"] == 1234
    # The provider name stays in `model`; it is not a feature.
    assert logged["model"] == "Groq"


def _tag_recorder(seen, reply="{}"):
    """Stand in for `generate_completion` and record the tag that was live when
    it was called — the same context `log_usage` runs in one line later."""
    async def _record(*args, **kwargs):
        seen.append(ut.current_query_type.get())
        return reply
    return _record


@pytest.mark.anyio
async def test_pdf_analysis_entry_point_tags_its_completions(monkeypatch):
    from ai import pdf_analysis

    seen = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _tag_recorder(seen))
    await pdf_analysis.analyze_uploaded_paper(
        "This paper studies intrusion detection on three public datasets.",
        custom_prompt="What datasets were used?",
    )

    assert seen and set(seen) == {"pdf_analysis"}


@pytest.mark.anyio
async def test_venue_recommendation_is_not_billed_as_manuscript(monkeypatch):
    """`venue` is its own tag and folds into `other` in the wallet — what it
    must not do is inflate the manuscript segment."""
    from ai import venue_recommendation

    seen = []
    monkeypatch.setattr(
        venue_recommendation, "generate_completion", _tag_recorder(seen, reply="[]"),
    )
    await venue_recommendation.recommend_venues(
        "We propose a transformer for intrusion detection on network traffic.",
        "computer science",
    )

    assert seen and set(seen) == {"venue"}
    # And the wallet has no `venue` bucket, so it must land in `other`.
    assert "venue" not in ut.FEATURE_BUCKETS


@pytest.mark.anyio
async def test_evidence_extraction_under_manuscript_stays_manuscript(monkeypatch):
    """The same extraction runs for a survey card and for a draft. Which one
    asked is what decides the bucket, and the outer caller is the one that
    asked."""
    from ai import evidence_extraction

    seen = []
    monkeypatch.setattr(evidence_extraction, "generate_completion", _tag_recorder(seen))

    paper = {"title": "A paper", "abstract": "We propose a method and report 12% gains."}
    with ut.query_type_scope("manuscript"):
        await evidence_extraction.extract_evidence(paper)

    assert seen and set(seen) == {"manuscript"}


@pytest.mark.anyio
async def test_the_same_extraction_alone_is_literature(monkeypatch):
    from ai import evidence_extraction

    seen = []
    monkeypatch.setattr(evidence_extraction, "generate_completion", _tag_recorder(seen))
    await evidence_extraction.extract_evidence(
        {"title": "Another paper", "abstract": "We propose a method and report 9% gains."}
    )

    assert seen and set(seen) == {"literature"}


# ─── the streaming shape ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_tagged_async_generator_holds_its_tag_across_yields():
    """A streamed manuscript section is billed while the response body is being
    iterated — long after the endpoint returned. The tag has to survive that."""
    seen = []

    @ut.tagged("manuscript")
    async def _stream():
        for _ in range(3):
            seen.append(ut.current_query_type.get())
            yield "chunk"

    async for _ in _stream():
        pass

    assert seen == ["manuscript", "manuscript", "manuscript"]
    assert ut.current_query_type.get() == "general"


@pytest.mark.anyio
async def test_a_tagged_generator_resets_when_closed_early():
    @ut.tagged("manuscript")
    async def _stream():
        for _ in range(10):
            yield "chunk"

    gen = _stream()
    await gen.__anext__()
    await gen.aclose()

    assert ut.current_query_type.get() == "general"


@pytest.mark.anyio
async def test_tagged_preserves_the_wrapped_name():
    @ut.tagged("literature")
    async def some_entry_point():
        return 1

    assert some_entry_point.__name__ == "some_entry_point"
    assert await some_entry_point() == 1
