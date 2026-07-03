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


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip_integration = pytest.mark.skip(
            reason="Integration test skipped by default. Run with: pytest -m integration --run-integration"
        )
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip_integration)
