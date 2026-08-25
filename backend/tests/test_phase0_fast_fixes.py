"""Phase 0 hour-scale fixes: allowlist, CORS, CSP, indexes, logging, prompts."""

import inspect
import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from ai import manuscript_generation, pdf_analysis
from ai.model_allowlist import (
    UnsupportedModelError,
    assert_allowed_model,
    is_allowed_model,
    require_allowed_model,
    resolve_groq_model,
)
from core.config import (
    CSP_POLICY,
    SECURITY_HEADERS,
    configure_logging,
    get_cors_origins,
)


class TestModelAllowlist:
    def test_product_defaults_are_allowed(self):
        assert is_allowed_model("groq", None)
        assert is_allowed_model("OpenRouter", "")
        assert is_allowed_model("gemini", "gemini-flash-latest")
        assert is_allowed_model("openrouter", "deepseek/deepseek-v3.2")

    def test_expensive_named_models_are_rejected(self):
        assert not is_allowed_model("openrouter", "anthropic/claude-opus-4")
        assert not is_allowed_model("openai", "o1-pro")
        assert not is_allowed_model("gemini", "gemini-2.5-pro")
        assert not is_allowed_model("not-a-provider", None)

    def test_require_raises(self):
        with pytest.raises(UnsupportedModelError):
            require_allowed_model("openrouter", "openai/gpt-4.1")

    def test_http_gate_is_400(self):
        with pytest.raises(HTTPException) as caught:
            assert_allowed_model("openrouter", "openai/gpt-4.1")
        assert caught.value.status_code == 400

    def test_retired_groq_llama_is_rewritten(self, monkeypatch):
        monkeypatch.delenv("GROQ_MODEL", raising=False)
        assert resolve_groq_model("llama-3.3-70b-versatile") == "openai/gpt-oss-120b"
        assert resolve_groq_model(None) == "openai/gpt-oss-120b"


class TestCorsOrigins:
    def test_default_origins_are_local_vite(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        origins = get_cors_origins()
        assert "http://localhost:5173" in origins
        assert "*" not in origins

    def test_env_is_trimmed_and_wildcards_dropped(self, monkeypatch):
        monkeypatch.setenv(
            "CORS_ORIGINS",
            " https://app.example.com/ , * ,not-a-url, http://127.0.0.1:5173 ",
        )
        assert get_cors_origins() == [
            "https://app.example.com",
            "http://127.0.0.1:5173",
        ]


class TestSecurityHeaders:
    def test_csp_is_present_and_locked_down(self):
        assert SECURITY_HEADERS["Content-Security-Policy"] == CSP_POLICY
        assert "default-src 'none'" in CSP_POLICY
        assert "frame-ancestors 'none'" in CSP_POLICY


class TestLogging:
    def test_root_logger_uses_rotating_file(self):
        configure_logging()
        root = logging.getLogger()
        assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)

    def test_windows_locked_rollover_does_not_raise(self):
        from core.config import _SafeRotatingFileHandler

        handler = _SafeRotatingFileHandler.__new__(_SafeRotatingFileHandler)
        with patch.object(RotatingFileHandler, "doRollover", side_effect=PermissionError("locked")):
            handler.doRollover()


class TestPromptsDoNotInventCharts:
    def test_generation_prompt_has_no_invented_bar_chart(self):
        prompt = manuscript_generation._prompt("topic", "results", "", "ieee")
        assert "bar [65.4, 78.2, 86.5, 92.8]" not in prompt
        assert "Do NOT invent quantitative charts" in prompt
        # C7: fabrication invariant lives on the system prompt, not the per-section user prompt.
        assert "NEVER fabricate experimental results" in manuscript_generation._NO_FABRICATE_RULE

    def test_abstract_prompt_has_no_chart_boilerplate(self):
        prompt = manuscript_generation._prompt("topic", "abstract", "", "ieee")
        assert "Do NOT invent quantitative charts" not in prompt
        assert "xychart-beta" not in prompt

    def test_pdf_chat_prompt_has_no_invented_bar_chart(self):
        source = inspect.getsource(pdf_analysis.analyze_uploaded_paper)
        assert "bar [62.1, 74.5, 88.0, 91.3]" not in source
        assert "Do NOT invent quantitative charts" in source
        assert 'C{"Quality OK?"}' in source


def _index_collection(existing=None):
    coll = MagicMock()
    coll.index_information = AsyncMock(return_value=existing or {})
    coll.drop_index = AsyncMock()
    coll.create_index = AsyncMock()
    return coll


def test_ensure_indexes_requests_quota_and_unique_manuscript():
    import asyncio

    usage = _index_collection()
    manuscripts = _index_collection({"user_id_1_topic_1": {"unique": False}})
    other = _index_collection()

    def getitem(name):
        if name == "usage_logs":
            return usage
        if name == "manuscripts":
            return manuscripts
        return other

    fake_db = MagicMock()
    fake_db.__getitem__.side_effect = getitem

    with patch("core.database.db", fake_db):
        from core.database import ensure_indexes

        asyncio.run(ensure_indexes())

    manuscripts.drop_index.assert_awaited_once_with("user_id_1_topic_1")
    manuscripts.create_index.assert_awaited()
    unique_call = manuscripts.create_index.await_args
    assert unique_call.args[0] == [("user_id", 1), ("topic", 1)]
    assert unique_call.kwargs.get("unique") is True
    usage.create_index.assert_awaited_once_with([("user_id", 1), ("date", 1)])


def test_ensure_indexes_replaces_unique_sources_index():
    """Render crash: sources already had unique user_id_1_topic_1, code wanted non-unique."""
    import asyncio

    sources = _index_collection({"user_id_1_topic_1": {"unique": True}})
    other = _index_collection()

    def getitem(name):
        if name == "sources":
            return sources
        return other

    fake_db = MagicMock()
    fake_db.__getitem__.side_effect = getitem

    with patch("core.database.db", fake_db):
        from core.database import ensure_indexes

        asyncio.run(ensure_indexes())

    sources.drop_index.assert_awaited_once_with("user_id_1_topic_1")
    sources.create_index.assert_awaited_once_with([("user_id", 1), ("topic", 1)])
    assert sources.create_index.await_args.kwargs.get("unique") is not True


def test_ensure_indexes_does_not_crash_on_index_conflict():
    import asyncio
    from pymongo.errors import OperationFailure

    boom = _index_collection()
    boom.create_index = AsyncMock(
        side_effect=OperationFailure("An existing index has the same name", 86)
    )

    fake_db = MagicMock()
    fake_db.__getitem__.side_effect = lambda _name: boom

    with patch("core.database.db", fake_db):
        from core.database import ensure_indexes

        asyncio.run(ensure_indexes())
