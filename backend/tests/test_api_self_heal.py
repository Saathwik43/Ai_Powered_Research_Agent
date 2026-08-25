"""
P1 API self-heal — rolling health, circuit breaker, one retry policy, one
source registry, and a fan-out that stops waiting on stragglers.

The behaviour that matters is what a user feels: a database that has been down
for ten minutes should not cost every search its full per-source timeout, and
one slow source should not decide how long everyone waits.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core import retry
from core.retry import backoff_delay, is_retryable, retry_after_seconds, with_retry
from integrations import paper_search as ps, registry
from services import api_health


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _breaker_on():
    api_health.reset()
    api_health.set_enabled(True)
    yield
    api_health.reset()
    api_health.set_enabled(False)


def _response(status: int, headers=None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("GET", "https://example.test")
    )


def _http_error(status: int, headers=None) -> httpx.HTTPStatusError:
    response = _response(status, headers)
    return httpx.HTTPStatusError("boom", request=response.request, response=response)


# ─── Rolling health and the breaker ────────────────────────────────────────────

class TestCircuitBreaker:
    def test_one_bad_call_does_not_open_a_circuit(self):
        api_health.record("Flaky", False)
        assert api_health.allow("Flaky") is True

    def test_a_source_that_is_failing_now_is_skipped(self):
        for _ in range(api_health.MIN_CALLS):
            api_health.record("Down", False)
        assert api_health.allow("Down") is False
        assert "retrying in" in api_health.blocked_reason("Down")
        assert api_health.health("Down")["state"] == api_health.STATE_OPEN

    def test_a_long_history_of_success_does_not_delay_noticing_an_outage(self):
        """The distinction this module exists for: 95% success since boot, and
        failing right now. Judging the whole window would need ninety more
        failures to outvote the morning's successes."""
        for _ in range(60):
            api_health.record("Mixed", True)
        for _ in range(api_health.EVAL_SAMPLE):
            api_health.record("Mixed", False)

        health = api_health.health("Mixed")
        assert health["window_calls"] == 80
        assert health["failure_rate"] == 1.0
        assert api_health.allow("Mixed") is False

    def test_recovery_is_tested_by_one_probe_not_the_whole_fan_out(self):
        for _ in range(api_health.MIN_CALLS):
            api_health.record("Recovering", False)
        # Pretend the cooldown elapsed.
        api_health._BREAKERS["Recovering"]["opened_at"] = time.time() - 999

        assert api_health.allow("Recovering") is True    # the probe
        assert api_health.allow("Recovering") is False   # everyone else waits

        api_health.record("Recovering", True)
        assert api_health.allow("Recovering") is True
        assert api_health.health("Recovering")["state"] == api_health.STATE_CLOSED

    def test_a_failed_probe_waits_longer_next_time(self):
        for _ in range(api_health.MIN_CALLS):
            api_health.record("StillDown", False)
        first_cooldown = api_health.health("StillDown")["cooldown"]

        api_health._BREAKERS["StillDown"]["opened_at"] = time.time() - 999
        assert api_health.allow("StillDown") is True
        api_health.record("StillDown", False)

        assert api_health.health("StillDown")["cooldown"] > first_cooldown
        assert api_health.allow("StillDown") is False

    def test_telemetry_feeds_the_window_without_call_site_changes(self):
        from services.api_telemetry import CallTracker

        for _ in range(api_health.MIN_CALLS):
            CallTracker("OpenAlex", "search").fail(error="timeout")
        assert api_health.health("OpenAlex")["failures"] == api_health.MIN_CALLS

    def test_zero_items_counts_as_a_failure_here_too(self):
        """API-6 made `succeed(items=0)` a failure for telemetry. The breaker
        reads the same signal, so a source answering 200-with-nothing forever
        is eventually skipped rather than searched forever."""
        from services.api_telemetry import CallTracker

        for _ in range(api_health.MIN_CALLS):
            CallTracker("DOAJ", "search").succeed(http_status=200, items=0)
        assert api_health.allow("DOAJ") is False


# ─── One retry policy ──────────────────────────────────────────────────────────

class TestRetryPolicy:
    def test_only_what_a_retry_can_fix_is_retried(self):
        assert is_retryable(_http_error(429)) is True
        assert is_retryable(_http_error(503)) is True
        assert is_retryable(httpx.ConnectError("no route")) is True
        # Asking again returns the same answer, so a retry is pure waste.
        assert is_retryable(_http_error(400)) is False
        assert is_retryable(_http_error(401)) is False
        assert is_retryable(_http_error(404)) is False

    def test_retry_after_is_honoured_when_the_server_sends_one(self):
        assert retry_after_seconds(_http_error(429, {"Retry-After": "7"})) == 7.0
        # The HTTP-date form needs a trustworthy clock on both ends.
        assert retry_after_seconds(_http_error(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
        # An absurd value is not honoured — it would exceed any request budget.
        assert retry_after_seconds(_http_error(429, {"Retry-After": "86400"})) is None

    def test_backoff_is_fully_jittered_not_clustered(self):
        """Equal or zero jitter leaves every concurrent retry landing in the
        same instant, which is the herd the backoff was meant to prevent."""
        samples = [backoff_delay(2, 0.4, 8.0) for _ in range(60)]
        assert min(samples) < 0.25 * max(samples)
        assert max(samples) <= min(8.0, 0.4 * 4)

    @pytest.mark.anyio
    async def test_a_retryable_failure_is_retried_then_succeeds(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise _http_error(503)
            return "ok"

        with patch.object(retry, "backoff_delay", lambda *a: 0.0):
            assert await with_retry(flaky, attempts=3) == "ok"
        assert calls == 3

    @pytest.mark.anyio
    async def test_a_permanent_failure_is_not_retried(self):
        calls = 0

        async def unauthorized():
            nonlocal calls
            calls += 1
            raise _http_error(401)

        with pytest.raises(httpx.HTTPStatusError):
            await with_retry(unauthorized, attempts=3)
        assert calls == 1

    @pytest.mark.anyio
    async def test_the_budget_stops_retrying_before_the_caller_gives_up(self):
        """A search has a latency budget; a source needing 30s of backoff has
        already lost its slot."""
        calls = 0

        async def rate_limited():
            nonlocal calls
            calls += 1
            raise _http_error(429, {"Retry-After": "10"})

        with pytest.raises(httpx.HTTPStatusError):
            await with_retry(rate_limited, attempts=5, budget=2.0)
        assert calls == 1


# ─── The registry and the fan-out ──────────────────────────────────────────────

class TestRegistry:
    def test_the_fan_out_and_the_reported_order_are_one_list(self):
        assert ps.SOURCE_NAMES == registry.source_names()
        assert "SemanticScholar" in ps.SOURCE_NAMES
        # Removed sources stay removed in both, because there is only one list.
        assert "BASE" not in ps.SOURCE_NAMES and "CORE" not in ps.SOURCE_NAMES

    def test_sources_resolve_through_the_patchable_module_namespace(self):
        """Late binding is deliberate: capturing the callable at import would
        bypass `patch("integrations.paper_search.arxiv_search", ...)` and let a
        unit test make real network calls."""
        source = next(s for s in registry.all_sources() if s.name == "arXiv")
        sentinel = object()
        with patch.object(ps, "arxiv_search", sentinel):
            assert source.resolve() is sentinel


class TestHedgedFanOut:
    @pytest.mark.anyio
    async def test_one_slow_source_does_not_set_everyone_s_latency(self):
        """Semantic Scholar's tail: eight sources answer quickly, one takes
        far longer, and the only stopping condition used to be the ceiling."""
        async def fast():
            return "fast"

        async def glacial():
            await asyncio.sleep(30)
            return "slow"

        tasks = {asyncio.ensure_future(fast()) for _ in range(ps.HEDGE_QUORUM)}
        straggler = asyncio.ensure_future(glacial())
        tasks.add(straggler)

        started = time.monotonic()
        done, pending = await ps._wait_with_grace(tasks, ceiling=30.0)
        elapsed = time.monotonic() - started

        assert len(done) >= ps.HEDGE_QUORUM
        assert straggler in pending
        assert elapsed < ps.HEDGE_GRACE_SECONDS + 2
        straggler.cancel()
        await asyncio.gather(straggler, return_exceptions=True)

    @pytest.mark.anyio
    async def test_below_the_quorum_the_full_budget_is_still_waited_out(self):
        """The hedge must not turn into 'give up early': with few sources
        answering, every one of them still matters."""
        async def slowish():
            await asyncio.sleep(0.3)
            return "ok"

        tasks = {asyncio.ensure_future(slowish()) for _ in range(2)}
        done, pending = await ps._wait_with_grace(tasks, ceiling=5.0)
        assert len(done) == 2 and not pending

    @pytest.mark.anyio
    async def test_a_source_with_an_open_circuit_is_reported_not_silently_dropped(self):
        for _ in range(api_health.MIN_CALLS):
            api_health.record("DOAJ", False)

        ps._cache.clear()
        ps._inflight.clear()
        empty = AsyncMock(return_value=[])
        patches = [
            # GitHub's client is blocking; the registry hands it to a thread, so
            # an AsyncMock there would come back as an un-awaited coroutine.
            patch.object(ps, s.attr, (lambda *a, **k: []) if s.sync else empty)
            for s in registry.all_sources()
        ]
        for p in patches:
            p.start()
        try:
            with patch("integrations.unpaywall.enrich_papers_with_oa",
                       AsyncMock(side_effect=lambda x: x)):
                _papers, sources = await ps._execute_search(
                    "anything", 10, False, 5.0, 1.0, set(), "k",
                )
        finally:
            for p in patches:
                p.stop()

        doaj = next(s for s in sources if s.name == "DOAJ")
        assert doaj.status == "skipped"
        assert "circuit open" in (doaj.error or "")
