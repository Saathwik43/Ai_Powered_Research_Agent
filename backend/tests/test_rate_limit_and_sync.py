"""0.6 / 0.15 / API-GH — rate limiting behind a proxy, and no clone on request.

0.15: on Render every request arrives from the platform proxy, so
`request.client.host` is one address for the whole internet and the anonymous
login budget was a single shared bucket. `X-Forwarded-For` carries the real
client, but only the hops our own proxy appended can be trusted — reading the
leftmost entry would let a caller mint a fresh bucket per request by sending a
made-up header.

0.6: nothing is unlimited by omission any more; routes opt out by declaring
their own budget.

API-GH: `POST /api/github/sync` shelled out to `git clone` inside a request.
"""

import importlib
import os
from unittest.mock import patch

import pytest
from fastapi import Request


def _request(client_host="203.0.113.9", forwarded=None):
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_host, 12345),
    })


def _limiter_with(hops):
    """Re-import core.limiter with TRUSTED_PROXY_HOPS set, since it is read once."""
    with patch.dict(os.environ, {"TRUSTED_PROXY_HOPS": str(hops)}):
        import core.limiter as limiter_module

        return importlib.reload(limiter_module)


@pytest.fixture(autouse=True)
def _restore_limiter_module():
    yield
    # Leave the module as the app imported it, or `app.state.limiter` and the
    # freshly reloaded one stop being the same object.
    import core.limiter

    importlib.reload(core.limiter)


def test_forwarded_header_is_ignored_when_no_proxy_is_configured():
    """The safe default. Directly exposed, `X-Forwarded-For` is pure client
    input and honouring it would make the limiter trivially bypassable."""
    mod = _limiter_with(0)
    assert mod.client_ip(_request(forwarded="1.2.3.4")) == "203.0.113.9"


def test_one_trusted_hop_reads_the_client_the_proxy_appended():
    mod = _limiter_with(1)
    assert mod.client_ip(_request(forwarded="198.51.100.7")) == "198.51.100.7"


def test_a_spoofed_prefix_cannot_shift_the_bucket():
    """Attacker sends `X-Forwarded-For: <random>`; the proxy appends the real
    address. With one trusted hop, the real one is what counts."""
    mod = _limiter_with(1)
    forged = "9.9.9.9, 8.8.8.8, 198.51.100.7"
    assert mod.client_ip(_request(forwarded=forged)) == "198.51.100.7"


def test_two_hops_counts_in_from_the_right():
    mod = _limiter_with(2)
    assert mod.client_ip(_request(forwarded="9.9.9.9, 198.51.100.7, 10.0.0.1")) == "198.51.100.7"


def test_hops_deeper_than_the_chain_do_not_index_out_of_range():
    mod = _limiter_with(5)
    assert mod.client_ip(_request(forwarded="198.51.100.7")) == "198.51.100.7"


def test_missing_header_falls_back_to_the_peer():
    mod = _limiter_with(1)
    assert mod.client_ip(_request()) == "203.0.113.9"


# ─── 0.6: a default budget exists, and per-route budgets still win ─────────────

def test_limiter_declares_a_default_limit():
    from core.limiter import limiter

    assert limiter._default_limits, "every route must have a budget by default"


def test_slowapi_middleware_is_installed():
    from slowapi.middleware import SlowAPIMiddleware

    from main import app

    assert any(m.cls is SlowAPIMiddleware for m in app.user_middleware)


def test_previously_unthrottled_routes_now_declare_a_budget():
    """These are the routes the audit called out as 'still thin'. Each fans out
    to an external API or an LLM, so each is worth its own budget rather than
    riding on the global default."""
    from main import app

    # Name-mangled: slowapi records decorated endpoints on the Limiter, and the
    # middleware skips exactly these so the per-route budget is the one applied.
    marked = app.state.limiter._Limiter__marked_for_limiting
    expected = [
        "get_venues", "get_guidelines", "arxiv_search_endpoint", "arxiv_feed",
        "arxiv_trending", "get_crossref_journals", "search_github",
        "get_github_repos", "get_github_categories", "get_github_papers",
        "admin_system_status", "admin_probe_one", "suggest_queries",
        "save_literature", "load_literature", "list_literature_surveys",
    ]
    marked_names = {name.rsplit(".", 1)[-1] for name in marked}
    missing = [name for name in expected if name not in marked_names]
    assert not missing, f"no explicit rate limit on: {missing}"


# ─── API-GH ────────────────────────────────────────────────────────────────────

def test_there_is_no_sync_route():
    from main import app

    paths = {
        route.path
        for router in app.routes
        for route in getattr(router, "routes", [router])
        if hasattr(route, "path")
    }
    assert "/api/github/sync" not in paths


def test_the_integration_module_no_longer_shells_out():
    import integrations.github_knowledge as gh

    source = open(gh.__file__, encoding="utf-8").read()
    assert "subprocess" not in source
    assert not hasattr(gh, "sync_repository")
    assert not hasattr(gh, "sync_all_repositories")


def test_the_deploy_script_still_knows_how_to_sync():
    from scripts.sync_github_repos import sync_all_repositories, sync_repository

    assert callable(sync_repository)
    assert callable(sync_all_repositories)


def test_unknown_repo_in_the_deploy_script_is_a_soft_failure():
    from scripts.sync_github_repos import sync_repository

    assert sync_repository("not-a-repo") is False
