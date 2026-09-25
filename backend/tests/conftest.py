# tests/conftest.py
# -----------------
# pytest configuration for the test suite.
#
# The `integration` mark is used for tests that make real network calls to
# external AI providers.  These are SKIPPED by default so the core suite runs
# deterministically without API keys.
#
# To run integration tests explicitly:
#     pytest -m integration --run-integration tests/

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that make real API calls (requires valid API keys).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests requiring real API keys "
        "(skipped by default; run with --run-integration).",
    )


@pytest.fixture(autouse=True, scope="session")
def _disable_shared_cache():
    """
    Keep the durable cache tier out of the suite.

    `core.shared_store` talks to the real Mongo handle. Tests patch `db` per
    module, so leaving the tier on would mean either a live connection attempt
    (a multi-second server-selection timeout on every cache read) or one
    module's fake collection answering another module's cache. Behaviour with
    the tier off is the documented degradation path: every call misses and the
    caller does the real work.
    """
    from core import shared_store

    shared_store.set_enabled(False)
    yield
    shared_store.set_enabled(True)


@pytest.fixture(autouse=True, scope="session")
def _disable_circuit_breaker():
    """
    Keep the circuit breaker out of the suite by default.

    `api_telemetry` feeds every tracked call into `api_health`, and the suite
    deliberately makes failing calls. Left on, a run's outcome would depend on
    collection order: whichever test failed a source five times would leave the
    breaker open for the next one, which would then see it skipped. The tests
    that are *about* the breaker turn it back on for themselves.
    """
    from services import api_health

    api_health.set_enabled(False)
    yield
    api_health.set_enabled(True)


@pytest.fixture(autouse=True, scope="session")
def _disable_search_time_briefs():
    """
    Keep card briefing out of every other endpoint's tests.

    `/api/literature` now briefs the head of its first page inline. That path
    calls a real provider, so with a key in the developer's `.env` every
    literature-endpoint test in the suite would make live LLM calls — slow,
    billed, and non-deterministic. Off, the endpoint returns exactly what it
    returned before: papers with no `brief` field. The tests that are *about*
    briefing turn it back on for themselves.
    """
    from ai import paper_brief

    paper_brief.set_enabled(False)
    yield
    paper_brief.set_enabled(True)


@pytest.fixture(autouse=True, scope="session")
def _disable_rate_limiter():
    """
    Turn the rate limiter off for the whole suite.

    Several endpoints are hit far more than their per-minute budget across a
    run. This used to be switched off as an import side effect of
    tests/test_guardrails.py, which meant whether a test saw a limiter depended
    on collection order.
    """
    from main import app

    previous = app.state.limiter.enabled
    app.state.limiter.enabled = False
    yield
    app.state.limiter.enabled = previous


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip_integration = pytest.mark.skip(
            reason="Integration test skipped by default. Run with: pytest -m integration --run-integration"
        )
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip_integration)
