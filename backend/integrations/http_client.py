"""
Shared pooled HTTP client for the search integrations.

Every integration used to open its own ``httpx.AsyncClient`` per call, so one
search paid eleven TCP handshakes and eleven TLS negotiations — roughly
100-300ms per source, all of it avoidable. Academic APIs are also repeat
endpoints: the same handful of hosts is hit on every single search, which is
exactly the case connection keep-alive exists for.

Usage
-----
    from integrations.http_client import get_client

    client = get_client()
    response = await client.get(url, params=params, timeout=10.0)

Pass ``timeout`` per request rather than per client — sources have very
different latency budgets (PubMed 5s, arXiv 15s) and they all share one pool.

Lifecycle
---------
The client is created lazily on first use and closed on app shutdown via
``aclose()`` (wired into main.py's lifespan). Creating it lazily rather than at
import time keeps the module import-safe for tests and scripts that never make
a request.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

logger = logging.getLogger(__name__)

# Sized for the fan-out: 11 sources in parallel, plus Unpaywall enrichment
# issuing concurrent DOI lookups on top.
_LIMITS = httpx.Limits(
    max_connections=64,
    max_keepalive_connections=32,
    keepalive_expiry=60.0,
)

# Per-request timeouts override this; it is only the backstop for callers that
# pass none.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)

_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


def get_client() -> httpx.AsyncClient:
    """
    The shared pooled client.

    Safe to call from any coroutine. The double-check is not strictly required
    under a single event loop — there is no await between the check and the
    assignment — but it keeps the contract obvious.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            limits=_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "ResearchAgent/1.0"},
        )
    return _client


class BoundClient:
    """
    The shared client with per-integration defaults applied.

    Integrations previously encoded their headers and timeout in their own
    ``AsyncClient`` constructor. Those settings are per-*source*, not
    per-connection, so they move here and are merged into each request instead
    — which lets every source share one connection pool while keeping its own
    latency budget and User-Agent.
    """

    __slots__ = ("_client", "_headers", "_timeout")

    def __init__(self, client: httpx.AsyncClient, headers: dict | None, timeout):
        self._client = client
        self._headers = headers
        self._timeout = timeout

    def _merged(self, kwargs: dict) -> dict:
        if self._headers:
            kwargs["headers"] = {**self._headers, **(kwargs.get("headers") or {})}
        if self._timeout is not None and kwargs.get("timeout") is None:
            kwargs["timeout"] = self._timeout
        return kwargs

    async def get(self, url, **kwargs):
        return await self._client.get(url, **self._merged(kwargs))

    async def post(self, url, **kwargs):
        return await self._client.post(url, **self._merged(kwargs))

    def stream(self, method, url, **kwargs):
        return self._client.stream(method, url, **self._merged(kwargs))


@asynccontextmanager
async def pooled_client(headers: dict | None = None, timeout=None):
    """
    Borrow the shared pool with *headers* and *timeout* as request defaults.

    Deliberately shaped like ``httpx.AsyncClient(...)`` so integrations read
    the same as before — but exiting the block returns the connection to the
    pool instead of tearing it down.
    """
    yield BoundClient(get_client(), headers, timeout)


async def aclose() -> None:
    """Close the pool. Called from the app lifespan on shutdown."""
    global _client
    async with _lock:
        if _client is not None and not _client.is_closed:
            await _client.aclose()
            logger.info("Shared HTTP connection pool closed.")
        _client = None
