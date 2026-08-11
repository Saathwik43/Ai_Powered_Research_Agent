import ipaddress
import socket
from urllib.parse import urlparse
from fastapi import HTTPException

BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


def assert_public_url(url_str: str) -> None:
    """Raise HTTPException if url_str points to a private/internal/link-local address."""
    parsed = urlparse(url_str)

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed.")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    if host.lower() in BLOCKED_HOSTS:
        raise HTTPException(status_code=400, detail="This URL is not allowed.")

    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Could not resolve host.")

    for family, _, _, _, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise HTTPException(status_code=400, detail="This URL is not allowed.")
