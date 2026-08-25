"""SSRF — a user-supplied URL must not reach the internal network.

Name-only validation left two ways through, both pinned here:

* the client re-resolved the hostname for the real request, so a 0-TTL record
  could answer publicly for the check and `169.254.169.254` a moment later, and
* a public URL answering `302 Location: http://127.0.0.1:.../` walked straight
  past a check that only looked at the URL the user typed.
"""

import socket
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from core import ssrf_guard


def _addrinfo(*ips, port=443):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)) for ip in ips]


def _resolving_to(*ips):
    return patch.object(ssrf_guard.socket, "getaddrinfo", lambda *a, **k: _addrinfo(*ips))


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",        # loopback
        "10.0.0.5",         # RFC1918
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",  # cloud metadata
        "0.0.0.0",
        "224.0.0.1",        # multicast
    ],
)
def test_private_targets_are_refused(ip):
    with _resolving_to(ip):
        with pytest.raises(HTTPException):
            ssrf_guard.pin_url("https://evil.example.com/x")


def test_public_target_is_pinned_to_the_resolved_address():
    with _resolving_to("93.184.216.34"):
        target = ssrf_guard.pin_url("https://example.com/paper.pdf?a=1")

    # The request is addressed to the literal IP, so nothing re-resolves the
    # name between the check and the connection.
    assert target.request_url == "https://93.184.216.34:443/paper.pdf?a=1"
    assert target.headers["Host"] == "example.com"
    # Certificate validation still happens against the real hostname.
    assert target.extensions["sni_hostname"] == "example.com"


def test_a_host_with_one_internal_answer_is_refused_entirely():
    """A name that resolves to both a routable and an internal address is a
    rebinding attempt with the setup already done."""
    with _resolving_to("93.184.216.34", "127.0.0.1"):
        with pytest.raises(HTTPException):
            ssrf_guard.pin_url("https://split-horizon.example.com/")


def test_ipv4_mapped_ipv6_loopback_is_refused():
    addrs = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::ffff:127.0.0.1", 443, 0, 0))]
    with patch.object(ssrf_guard.socket, "getaddrinfo", lambda *a, **k: addrs):
        with pytest.raises(HTTPException):
            ssrf_guard.pin_url("https://sneaky.example.com/")


@pytest.mark.parametrize("url", [
    "http://example.com:6379/",     # Redis
    "http://example.com:22/",       # SSH
    "http://example.com:2375/",     # Docker
])
def test_non_web_ports_are_refused(url):
    with _resolving_to("93.184.216.34"):
        with pytest.raises(HTTPException) as exc:
            ssrf_guard.pin_url(url)
    assert "port" in str(exc.value.detail).lower()


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/x",
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(HTTPException):
        ssrf_guard.pin_url(url)


def test_localhost_by_name_is_refused_before_resolution():
    with pytest.raises(HTTPException):
        ssrf_guard.pin_url("http://localhost/admin")


def test_unresolvable_host_is_a_400():
    def _boom(*a, **k):
        raise socket.gaierror("nope")

    with patch.object(ssrf_guard.socket, "getaddrinfo", _boom):
        with pytest.raises(HTTPException) as exc:
            ssrf_guard.pin_url("https://nowhere.invalid/")
    assert exc.value.status_code == 400


def test_assert_public_url_still_accepts_a_public_host():
    with _resolving_to("93.184.216.34"):
        ssrf_guard.assert_public_url("https://example.com/")


# ─── Redirects and size, through safe_fetch ────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=(b"ok",)):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        return None


class _FakeClient:
    """Replays a scripted list of responses, recording the URLs asked for."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, extensions=None):
        self.requested.append(url)
        return type("Req", (), {"url": url, "headers": headers or {}})()

    async def send(self, request, stream=False):
        return self.responses.pop(0)


def _client_factory(client):
    return lambda *a, **k: client


@pytest.mark.anyio
async def test_redirect_into_the_metadata_service_is_refused():
    client = _FakeClient([
        _FakeResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"}),
    ])
    resolutions = {"public.example.com": "93.184.216.34", "169.254.169.254": "169.254.169.254"}

    def _resolve(host, port, **kwargs):
        return _addrinfo(resolutions[host], port=port)

    with patch.object(ssrf_guard.httpx, "AsyncClient", _client_factory(client)), \
         patch.object(ssrf_guard.socket, "getaddrinfo", _resolve):
        with pytest.raises(HTTPException):
            await ssrf_guard.safe_fetch("http://public.example.com/x", max_bytes=1024)


@pytest.mark.anyio
async def test_a_public_redirect_is_followed():
    client = _FakeClient([
        _FakeResponse(302, {"location": "https://cdn.example.com/paper.pdf"}),
        _FakeResponse(200, {"content-type": "application/pdf"}, chunks=(b"%PDF-1.4",)),
    ])

    def _resolve(host, port, **kwargs):
        return _addrinfo("93.184.216.34", port=port)

    with patch.object(ssrf_guard.httpx, "AsyncClient", _client_factory(client)), \
         patch.object(ssrf_guard.socket, "getaddrinfo", _resolve):
        response = await ssrf_guard.safe_fetch("https://example.com/paper", max_bytes=1024)

    assert response.content == b"%PDF-1.4"
    # Both hops connected to the literal address, not the name.
    assert all(r.startswith("https://93.184.216.34:443/") for r in client.requested)


@pytest.mark.anyio
async def test_declared_oversize_is_refused_before_reading():
    client = _FakeClient([
        _FakeResponse(200, {"content-length": str(50 * 1024 * 1024)}, chunks=(b"x" * 10,)),
    ])
    with patch.object(ssrf_guard.httpx, "AsyncClient", _client_factory(client)), \
         patch.object(ssrf_guard.socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34")):
        with pytest.raises(HTTPException) as exc:
            await ssrf_guard.safe_fetch("https://example.com/big", max_bytes=1024)
    assert exc.value.status_code == 413


@pytest.mark.anyio
async def test_undeclared_oversize_is_cut_off_mid_stream():
    """A chunked response declares no length, so the byte budget is the only
    thing standing between the worker and an unbounded download."""
    client = _FakeClient([
        _FakeResponse(200, {}, chunks=(b"x" * 800, b"x" * 800, b"x" * 800)),
    ])
    with patch.object(ssrf_guard.httpx, "AsyncClient", _client_factory(client)), \
         patch.object(ssrf_guard.socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34")):
        with pytest.raises(HTTPException) as exc:
            await ssrf_guard.safe_fetch("https://example.com/stream", max_bytes=1024)
    assert exc.value.status_code == 413


@pytest.mark.anyio
async def test_redirect_chain_is_bounded():
    client = _FakeClient([
        _FakeResponse(302, {"location": f"https://example.com/hop{i}"}) for i in range(10)
    ])
    with patch.object(ssrf_guard.httpx, "AsyncClient", _client_factory(client)), \
         patch.object(ssrf_guard.socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34")):
        with pytest.raises(HTTPException):
            await ssrf_guard.safe_fetch("https://example.com/start", max_bytes=1024)
