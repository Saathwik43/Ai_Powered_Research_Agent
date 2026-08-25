"""Fetching a URL a user supplied, without letting it reach the internal network.

Validating the hostname and then handing the *hostname* to an HTTP client is not
enough. Two gaps the old `assert_public_url` left open:

* **DNS rebinding.** The check resolved `evil.com`, saw a public address, and
  approved it. The client then resolved `evil.com` *again* for the actual
  request — and a 0-TTL record can answer `169.254.169.254` the second time.
  Nothing in between noticed.
* **Redirects.** A public URL that answers `302 Location: http://127.0.0.1:6379/`
  walks straight past a check that only ever looked at the URL the user typed.

So the connection is pinned: resolve once, verify the address, then connect to
*that address* with the original hostname carried in the `Host` header and in
the TLS SNI (so certificate validation is unaffected). Redirects are followed by
hand, each hop re-validated from scratch, and the response body is read against
a byte budget rather than buffered whole.
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

__all__ = ["assert_public_url", "pin_url", "safe_fetch", "PinnedTarget", "SsrfError"]

BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata.goog", "instance-data"}

# Anything other than plain web traffic is out of scope for "read this article",
# and non-web ports are how an SSRF turns into "talk to Redis / the Docker
# socket / an unauthenticated admin panel".
ALLOWED_PORTS = {80, 443}

MAX_REDIRECTS = 3


class SsrfError(HTTPException):
    def __init__(self, detail: str = "This URL is not allowed."):
        super().__init__(status_code=400, detail=detail)


@dataclass
class PinnedTarget:
    """A URL rewritten to connect to one already-validated IP address."""

    original_url: str
    host: str
    ip: str
    port: int
    scheme: str
    request_url: str
    headers: dict = field(default_factory=dict)
    extensions: dict = field(default_factory=dict)


def _is_forbidden(ip: ipaddress._BaseAddress) -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    # An IPv4 address wearing an IPv6 costume (::ffff:127.0.0.1, 64:ff9b::/96)
    # passes every check above while still routing to the loopback interface.
    mapped = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    if mapped is not None and _is_forbidden(mapped):
        return True
    return False


def pin_url(url_str: str) -> PinnedTarget:
    """Validate *url_str* and return a target pinned to the address it resolved to.

    Raises ``SsrfError`` for a non-web scheme or port, an unresolvable host, or
    any host that resolves to a non-public address.
    """
    parsed = urlparse(url_str)

    if parsed.scheme not in ("http", "https"):
        raise SsrfError("Only http/https URLs are allowed.")

    host = parsed.hostname
    if not host:
        raise SsrfError("Invalid URL.")

    if host.lower() in BLOCKED_HOSTS:
        raise SsrfError()

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise SsrfError(f"Only ports {sorted(ALLOWED_PORTS)} are allowed.")

    try:
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise SsrfError("Could not resolve host.")

    if not addrs:
        raise SsrfError("Could not resolve host.")

    # Every answer must be public, not just the one we happen to pick: a host
    # that returns one routable address and one internal one is a rebinding
    # attempt with the work already done for it.
    chosen = None
    for _family, _type, _proto, _canon, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_forbidden(ip):
            raise SsrfError()
        if chosen is None:
            chosen = ip

    assert chosen is not None  # non-empty addrs and none forbidden

    # Rewrite the authority to the literal address so the client cannot re-resolve.
    literal = f"[{chosen}]" if chosen.version == 6 else str(chosen)
    netloc = f"{literal}:{port}"
    request_url = urlunparse(
        (parsed.scheme, netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )

    headers = {"Host": parsed.netloc.split("@")[-1]}
    # httpcore honours this for both the TLS handshake and certificate
    # validation, so pinning the address does not downgrade cert checking.
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}

    return PinnedTarget(
        original_url=url_str,
        host=host,
        ip=str(chosen),
        port=port,
        scheme=parsed.scheme,
        request_url=request_url,
        headers=headers,
        extensions=extensions,
    )


def assert_public_url(url_str: str) -> None:
    """Raise if *url_str* points anywhere but the public internet.

    Kept for callers that only need the check. Prefer ``safe_fetch``: this
    validates the name, but whoever connects afterwards resolves it again.
    """
    pin_url(url_str)


async def safe_fetch(
    url_str: str,
    *,
    max_bytes: int,
    timeout: float = 10.0,
    accept: str | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> httpx.Response:
    """GET *url_str* with the address pinned and every redirect re-validated.

    Reads at most *max_bytes*; a larger body raises 413 rather than being
    buffered. Returns a response whose ``.content`` is already loaded.
    """
    current = url_str
    seen = set()

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _hop in range(max_redirects + 1):
            if current in seen:
                raise SsrfError("Redirect loop.")
            seen.add(current)

            target = pin_url(current)
            headers = dict(target.headers)
            if accept:
                headers["Accept"] = accept

            request = client.build_request(
                "GET", target.request_url, headers=headers, extensions=target.extensions
            )
            response = await client.send(request, stream=True)

            if response.is_redirect:
                location = response.headers.get("location", "")
                await response.aclose()
                if not location:
                    raise SsrfError("Redirect without a destination.")
                # Resolved against the *original* URL, not the pinned literal,
                # so a relative Location keeps the real hostname.
                current = str(httpx.URL(target.original_url).join(location))
                continue

            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                await response.aclose()
                raise HTTPException(
                    status_code=413,
                    detail=f"Remote content is larger than the {max_bytes // (1024 * 1024)}MB limit.",
                )

            chunks = []
            total = 0
            try:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Remote content is larger than the {max_bytes // (1024 * 1024)}MB limit.",
                        )
                    chunks.append(chunk)
            finally:
                await response.aclose()

            # Hand back a normal, fully-read response so callers can use
            # .text / .content / .raise_for_status() as usual.
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                request=request,
            )

    raise SsrfError("Too many redirects.")
