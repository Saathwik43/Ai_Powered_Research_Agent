"""ASGI entrypoint: builds the app, wires middleware, mounts the routers.

Route handlers live in ``routers/`` — one module per domain. ``core.config`` is
imported first so ``.env`` and logging are in place before any service module
reads settings at import time.
"""

from core.config import get_cors_origins  # noqa: F401 — loads .env + logging on import

import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from core.auth import seed_admin
from core.limiter import limiter
from core.database import ensure_indexes, ping_db
from routers import admin, auth, discovery, manuscript, pdf

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ping_db()
    await ensure_indexes()
    await seed_admin()
    try:
        from services.admin_status import _ensure_disabled_loaded
        await _ensure_disabled_loaded()
    except Exception:
        logger.warning("Could not load admin source toggles on startup", exc_info=True)
    yield
    # Return the shared search connection pool cleanly on shutdown.
    from integrations.http_client import aclose as close_http_pool
    await close_http_pool()

app = FastAPI(title="AI-Powered Research Paper Publishing Agent", lifespan=lifespan, debug=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

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
