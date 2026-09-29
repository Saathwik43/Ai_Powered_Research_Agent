"""ASGI entrypoint: builds the app, wires middleware, mounts the routers.

Route handlers live in ``routers/`` — one module per domain. ``core.config`` is
imported first so ``.env`` and logging are in place before any service module
reads settings at import time.
"""

from core.config import (  # noqa: F401 — loads .env + logging on import
    MAX_REQUEST_BODY_BYTES,
    SECURITY_HEADERS,
    get_cors_origins,
)

import asyncio
import logging
import sys
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from core.auth import backfill_email_verified, seed_admin
from core.limiter import limiter
from core.request_limits import BodySizeLimitMiddleware
from core.database import ensure_indexes, ping_db
from routers import admin, auth, discovery, manuscript, pdf

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ping_db()
    await ensure_indexes()
    await seed_admin()
    # Login now requires a verified address; accounts created before that rule
    # existed carry no such field and would all be locked out (see 0.4).
    await backfill_email_verified()
    try:
        from services.admin_status import _ensure_disabled_loaded
        await _ensure_disabled_loaded()
    except Exception:
        logger.warning("Could not load admin source toggles on startup", exc_info=True)
    # The Anthology search reads a cached bibliography (~13 MB). Start the
    # download now so the first literature search is not the one that pays for it.
    acl_warm = None
    if "pytest" not in sys.modules:
        from integrations.acl import warm_index
        acl_warm = asyncio.create_task(warm_index())
    yield
    if acl_warm is not None:
        acl_warm.cancel()
        try:
            await acl_warm
        except (asyncio.CancelledError, Exception):
            pass
        from integrations.acl import close_index
        await close_index()
    # Return the shared search connection pool cleanly on shutdown.
    from integrations.http_client import aclose as close_http_pool
    await close_http_pool()

app = FastAPI(title="AI-Powered Research Paper Publishing Agent", lifespan=lifespan, debug=False)

# ─── Middleware ────────────────────────────────────────────────────────────────
# Starlette builds the stack so that the LAST middleware added is the OUTERMOST.
# Ordering matters here: CORS has to wrap the two rejecting layers below, or a
# 429 or 413 reaches the browser without CORS headers and the SPA reports it as
# an unexplained network failure instead of showing the real reason.
#
#   CORS  →  body-size cap  →  rate limit  →  security headers  →  routes

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# Applies `DEFAULT_RATE_LIMIT` to every route that has not declared its own
# `@limiter.limit`. slowapi skips routes it sees as explicitly marked, so the
# per-route budgets still win where they exist (0.6).
app.add_middleware(SlowAPIMiddleware)

# Refuse an oversized request before multipart parsing or JSON buffering
# allocates anything for it (0.9).
app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=MAX_REQUEST_BODY_BYTES)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.get("/")
async def root():
    return {"message": "Welcome to the Research Agent API"}


app.include_router(auth.router)
app.include_router(discovery.router)
app.include_router(manuscript.router)
app.include_router(pdf.router)
app.include_router(admin.router)
