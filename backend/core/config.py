"""Environment and logging bootstrap.

Import this before anything that reads settings at import time (auth, database,
ai.llm_provider, the integration clients). ``main`` and ``routers`` both pull it
in first so the ordering holds no matter which entrypoint loads the app.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
import os
import re
from logging.handlers import RotatingFileHandler


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Rotate when we can. On Windows, uvicorn --reload keeps the previous
    worker's handle open, so rename(backend.log → backend.log.1) raises
    WinError 32. Drop that failure and keep writing to the current file."""

    def doRollover(self):
        try:
            super().doRollover()
        except (PermissionError, OSError):
            pass

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_ORIGIN_RE = re.compile(r"^https?://[^/\s]+$")

DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]

# API responses are JSON, not HTML. A lock-down CSP still stops a browser that
# is pointed at the API origin from running injected script, and is the header
# the audit asked for. The SPA origin needs its own policy (see index.html).
CSP_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": CSP_POLICY,
}


def configure_logging() -> None:
    """Stdout (for the host log drain) plus a rotating local file."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    has_stream = False
    has_rotating = False
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler):
            has_rotating = True
        elif isinstance(handler, logging.StreamHandler):
            has_stream = True

    if not has_stream:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if not has_rotating:
        try:
            file_handler = _SafeRotatingFileHandler(
                "backend.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            # Ephemeral / read-only filesystems (e.g. some PaaS) — stdout is enough.
            pass


configure_logging()


def get_cors_origins() -> list:
    """Trim, drop empties/wildcards, keep only http(s) origins with a host."""
    cors_origins_env = os.getenv("CORS_ORIGINS")
    raw = cors_origins_env.split(",") if cors_origins_env else DEFAULT_CORS_ORIGINS
    origins: list[str] = []
    seen: set[str] = set()
    for item in raw:
        origin = item.strip().rstrip("/")
        if not origin or origin == "*":
            continue
        if not _ORIGIN_RE.match(origin):
            continue
        if origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins
