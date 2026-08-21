"""Server-side (provider, model) allowlist.

Request bodies used to pass provider/model straight through to the billed
API. A caller could name an arbitrarily expensive OpenRouter/OpenAI model
on our key. This module is the single gate: HTTP handlers reject before
the stream opens, and generate/stream still check as a backstop.

`model=None` (or blank) means "this provider's default from env" — that is
allowed for every known provider. Named models must match a pair the product
actually offers.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Groq retired these IDs on 2026-08-16 (free/developer tier). A request with
# the old name returns HTTP 404; map them so a stale GROQ_MODEL still works.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_RETIRED_MODELS = {
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "llama-3.1-70b-versatile": "openai/gpt-oss-120b",
    "llama-3.3-70b-specdec": "openai/gpt-oss-120b",
}
_GROQ_RETIRED_WARNED: set[str] = set()

# Providers the product can call. Unknown names are rejected even with no model.
ALLOWED_PROVIDERS = frozenset({
    "groq",
    "openrouter",
    "nvidia",
    "openai",
    "gemini",
    "cerebras",
    "huggingface",
    "mistral",
})

# Explicit model IDs offered in the UI. Keep in step with frontend/src/constants/models.js.
ALLOWED_NAMED_MODELS = frozenset({
    ("openrouter", "deepseek/deepseek-v3.2"),
    ("openrouter", "moonshotai/kimi-k2.6"),
    ("gemini", "gemini-2.0-flash"),
    ("gemini", "gemini-flash-latest"),
    ("gemini", "gemma-4-27b-it"),
    ("gemini", "gemma-4-26b-a4b-it"),
    ("groq", "openai/gpt-oss-120b"),
    ("groq", "openai/gpt-oss-20b"),
    ("groq", "qwen/qwen3.6-27b"),
})


def resolve_groq_model(requested: str | None = None) -> str:
    """Env default, with retired Llama IDs rewritten to Groq's replacement."""
    raw = (requested or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL).strip()
    mapped = GROQ_RETIRED_MODELS.get(raw, raw)
    if mapped != raw and raw not in _GROQ_RETIRED_WARNED:
        _GROQ_RETIRED_WARNED.add(raw)
        logger.warning("Groq model %s was retired; using %s", raw, mapped)
    return mapped


class UnsupportedModelError(ValueError):
    """Raised when a request names a provider/model we will not call."""


def normalize_pair(provider: str | None, model: str | None) -> tuple[str, str | None]:
    p = (provider or "").strip().lower()
    m = (model or "").strip() or None
    return p, m


def is_allowed_model(provider: str | None, model: str | None) -> bool:
    p, m = normalize_pair(provider, model)
    if not p or p not in ALLOWED_PROVIDERS:
        return False
    if m is None:
        return True
    return (p, m) in ALLOWED_NAMED_MODELS


def require_allowed_model(provider: str | None, model: str | None) -> tuple[str, str | None]:
    """Return the normalised pair, or raise UnsupportedModelError."""
    p, m = normalize_pair(provider, model)
    if not is_allowed_model(p, m):
        raise UnsupportedModelError(
            f"Unsupported provider/model '{p or '(none)'}' / '{m or '(default)'}'."
        )
    return p, m


def assert_allowed_model(provider: str | None, model: str | None) -> tuple[str, str | None]:
    """HTTP-facing gate. 400 so the stream never opens on a rejected pair."""
    try:
        return require_allowed_model(provider, model)
    except UnsupportedModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
