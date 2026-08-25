"""
Tests for the Gemini context cache in ai/llm_provider.py.

The cache existed but could never activate: the size gate was 32768 tokens (the
gemini-1.5 minimum) while the estimator under-counted, so PDF chat -- whose
context is capped at 40 000 chars -- scored ~8.7k and was refused every time.
The full context was therefore re-sent on every single chat message.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai import llm_provider
from ai.llm_provider import (
    _GEMINI_CACHE_MIN_TOKENS,
    estimate_tokens,
    get_or_create_gemini_cache,
)


@pytest.fixture(autouse=True)
def clear_cache_state():
    llm_provider._gemini_caches.clear()
    llm_provider._gemini_cache_failures.clear()
    yield
    llm_provider._gemini_caches.clear()
    llm_provider._gemini_cache_failures.clear()


def _fake_client(cache_name="cachedContents/abc123"):
    client = MagicMock()
    client.aio.caches.create = AsyncMock(return_value=MagicMock(name_=cache_name))
    client.aio.caches.create.return_value.name = cache_name
    return client


class TestSizeGate:
    def test_estimator_uses_characters_not_word_count(self):
        """`words * 1.3` under-counts dense academic prose by roughly a third."""
        text = "x" * 40000
        assert estimate_tokens(text) == 10000

    def test_gate_is_the_modern_flash_minimum_not_the_1_5_minimum(self):
        assert _GEMINI_CACHE_MIN_TOKENS == 2048, (
            "32768 is the gemini-1.5 floor; the default model here is flash-class"
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize("chars, should_cache", [
        (40000, True),    # PDF chat cap -- the case that never worked
        (20000, True),    # typical PDF chat context
        (9000, True),     # small paper, just over the floor
        (6000, False),    # below the floor, not worth a cache
        (1200, False),    # abstract-only
    ])
    async def test_gate_opens_for_realistic_contexts(self, chars, should_cache):
        client = _fake_client()
        with patch.object(llm_provider, "_gemini_client", client):
            result = await get_or_create_gemini_cache("k", "sys", "x" * chars)

        assert bool(result) is should_cache
        assert client.aio.caches.create.await_count == (1 if should_cache else 0)


class TestCacheKeying:
    @pytest.mark.anyio
    async def test_handle_is_reused_within_ttl(self):
        client = _fake_client()
        with patch.object(llm_provider, "_gemini_client", client):
            first = await get_or_create_gemini_cache("k", "sys", "x" * 20000)
            second = await get_or_create_gemini_cache("k", "sys", "x" * 20000)

        assert first == second
        assert client.aio.caches.create.await_count == 1

    @pytest.mark.anyio
    async def test_key_is_scoped_by_model(self):
        """
        A cached-content handle is bound to the model that created it, so a
        handle made for flash must never be handed to a pro request.
        """
        client = _fake_client()
        with patch.object(llm_provider, "_gemini_client", client):
            await get_or_create_gemini_cache("k", "sys", "x" * 20000, model="gemini-flash-latest")
            await get_or_create_gemini_cache("k", "sys", "x" * 20000, model="gemini-pro-latest")

        assert client.aio.caches.create.await_count == 2

    @pytest.mark.anyio
    async def test_expired_entries_are_not_reused(self):
        client = _fake_client()
        with patch.object(llm_provider, "_gemini_client", client):
            await get_or_create_gemini_cache("k", "sys", "x" * 20000)
            # Age the stored entry past its expiry.
            key = next(iter(llm_provider._gemini_caches))
            name, _expiry = llm_provider._gemini_caches[key]
            llm_provider._gemini_caches[key] = (name, time.time() - 1)

            await get_or_create_gemini_cache("k", "sys", "x" * 20000)

        assert client.aio.caches.create.await_count == 2


class TestFailureHandling:
    @pytest.mark.anyio
    async def test_creation_failure_is_negatively_cached(self):
        """
        A context the API refuses must not cost a failed round-trip on every
        subsequent request.
        """
        client = MagicMock()
        client.aio.caches.create = AsyncMock(side_effect=RuntimeError("too small"))
        with patch.object(llm_provider, "_gemini_client", client):
            for _ in range(5):
                assert await get_or_create_gemini_cache("k", "sys", "x" * 20000) is None

        assert client.aio.caches.create.await_count == 1

    @pytest.mark.anyio
    async def test_no_api_key_returns_none_without_calling(self):
        with patch.object(llm_provider, "_gemini_client", None), \
             patch.dict("os.environ", {}, clear=True):
            assert await get_or_create_gemini_cache("k", "sys", "x" * 20000) is None


class TestAutoCascadeLazyCache:
    """
    In auto mode the provider is not known up front, so the cache is passed down
    as a resolver and materialised only on the Gemini leg. Creating it eagerly
    would pay for a cache the run may never touch.
    """

    @staticmethod
    async def _drain(**kwargs):
        legs = []

        async def fake_stream(system_prompt, user_prompt, mt, temp, provider, model, cached):
            legs.append({"provider": provider, "cached": bool(cached), "prompt": user_prompt})
            if provider == "openai":
                yield {"type": "stopped", "reason": "error", "message": "no key"}
            else:
                yield {"type": "chunk", "text": "drafted."}
                yield {"type": "done"}

        with patch.object(llm_provider, "stream_completion", new=fake_stream):
            async for _ in llm_provider.stream_completion_auto(
                "sys", "PROMPT WITH CONTEXT", 1000, 0.4, **kwargs
            ):
                pass
        return legs

    @pytest.mark.anyio
    async def test_resolver_runs_only_on_the_gemini_leg(self):
        calls = {"n": 0}

        async def resolver():
            calls["n"] += 1
            return "cachedContents/abc"

        legs = await self._drain(
            gemini_cache_resolver=resolver, user_prompt_cached="PROMPT WITHOUT CONTEXT"
        )

        assert [leg["provider"] for leg in legs] == ["openai", "gemini"]
        assert calls["n"] == 1, "cache was created for a provider that never ran"
        assert legs[0]["cached"] is False
        assert legs[1]["cached"] is True

    @pytest.mark.anyio
    async def test_cached_leg_drops_the_inline_context(self):
        """Sending the context inline *and* via the cache would double the cost."""
        legs = await self._drain(
            gemini_cache_resolver=AsyncMock(return_value="cachedContents/abc"),
            user_prompt_cached="PROMPT WITHOUT CONTEXT",
        )
        gemini_leg = next(leg for leg in legs if leg["provider"] == "gemini")
        assert gemini_leg["prompt"] == "PROMPT WITHOUT CONTEXT"

    @pytest.mark.anyio
    async def test_context_stays_inline_when_the_cache_cannot_be_made(self):
        legs = await self._drain(
            gemini_cache_resolver=AsyncMock(return_value=None),
            user_prompt_cached="PROMPT WITHOUT CONTEXT",
        )
        gemini_leg = next(leg for leg in legs if leg["provider"] == "gemini")
        assert gemini_leg["cached"] is False
        assert gemini_leg["prompt"] == "PROMPT WITH CONTEXT"

    @pytest.mark.anyio
    async def test_resolver_failure_falls_back_to_inline_context(self):
        legs = await self._drain(
            gemini_cache_resolver=AsyncMock(side_effect=RuntimeError("api down")),
            user_prompt_cached="PROMPT WITHOUT CONTEXT",
        )
        gemini_leg = next(leg for leg in legs if leg["provider"] == "gemini")
        assert gemini_leg["cached"] is False
        assert gemini_leg["prompt"] == "PROMPT WITH CONTEXT"

    @pytest.mark.anyio
    async def test_no_resolver_behaves_exactly_as_before(self):
        legs = await self._drain()
        assert all(leg["prompt"] == "PROMPT WITH CONTEXT" for leg in legs)
        assert all(leg["cached"] is False for leg in legs)
