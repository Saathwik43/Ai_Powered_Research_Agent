"""
The LLM cascade consults the circuit breaker before trying a provider.

Every provider call already went through `track_call`, which feeds
`api_health.record(...)` — so the breaker has been watching the cascade all
along and was simply never asked. The consequence was visible in a production
log: one request paid a 401 from OpenAI, a 403 from Mistral, a 404 from
Cerebras and a dead HuggingFace model, in that order, before reaching a
provider that could have answered. Every request paid it, because a revoked key
is revoked for all of them.

The subtle part is `api_health.allow`, which is **not** side-effect free: for an
open breaker past its cooldown it claims the half-open probe. A claim that is
never followed by a recorded outcome leaves that provider blocked permanently,
because nothing but a recorded call can clear it.
"""

import httpx
import pytest

from ai import llm_provider
from services import api_health, api_telemetry


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _breaker_on():
    api_health.reset()
    api_health.set_enabled(True)
    yield
    api_health.reset()
    api_health.set_enabled(False)


def _http_error(status: int) -> httpx.HTTPStatusError:
    """The real shape a provider failure arrives in.

    It matters that these carry a status: `_should_retry_llm` does not retry a
    4xx, so a 401 costs one call, not two with a 2s sleep between them. A bare
    exception would make the test both slower and less like production.
    """
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def _trip(provider_name: str, calls: int = 10) -> str:
    """Drive a provider's breaker open through the real recording path."""
    key = llm_provider._health_key(provider_name)
    for _ in range(calls):
        api_health.record(key, False)
    return key


class TestHealthKeyMatchesTelemetry:
    """If the gate reads a different key than `track_call` writes, it is
    reading some other provider's history — silently, and forever."""

    @pytest.mark.parametrize("provider_name, telemetry_name", [
        ("OpenAI", "OpenAI"),
        ("Gemini", "Google Gemini"),
        ("Groq", "Groq"),
        ("Cerebras", "Cerebras"),
        ("Mistral", "Mistral"),
        ("HuggingFace", "Hugging Face Inference"),
    ])
    def test_gate_and_recorder_agree(self, provider_name, telemetry_name):
        assert llm_provider._health_key(provider_name) == api_telemetry.canonical_name(
            telemetry_name
        )

    def test_a_tripped_provider_reads_as_blocked(self):
        _trip("OpenAI")
        blocked, reason = llm_provider._provider_is_blocked("OpenAI")
        assert blocked is True
        assert "circuit open" in reason

    def test_an_untouched_provider_reads_as_live(self):
        assert llm_provider._provider_is_blocked("Gemini") == (False, None)


class TestProbeIsNeverClaimedAndDropped:
    """`allow()` claims the half-open probe; the claim must reach a call."""

    def test_a_healthy_provider_never_consults_allow(self, monkeypatch):
        """A closed breaker is answered by `blocked_reason` alone, so no probe
        is ever claimed for a provider that is working."""
        called = []
        monkeypatch.setattr(api_health, "allow", lambda name: called.append(name) or True)

        assert llm_provider._provider_is_blocked("Gemini") == (False, None)
        assert called == [], "allow() must not be consulted for a closed breaker"

    def test_probe_is_let_through_once_the_cooldown_elapses(self, monkeypatch):
        key = _trip("Mistral")
        assert llm_provider._provider_is_blocked("Mistral")[0] is True

        # move past the cooldown without sleeping through it
        breaker = api_health._BREAKERS[key]
        breaker["opened_at"] -= breaker["cooldown"] + 1

        blocked, _reason = llm_provider._provider_is_blocked("Mistral")
        assert blocked is False, "the probe must be let through"
        # and the claim is exclusive: a second caller in the same moment waits
        assert llm_provider._provider_is_blocked("Mistral")[0] is True

    def test_a_recorded_success_clears_the_claim(self):
        key = _trip("Mistral")
        breaker = api_health._BREAKERS[key]
        breaker["opened_at"] -= breaker["cooldown"] + 1

        assert llm_provider._provider_is_blocked("Mistral")[0] is False
        api_health.record(key, True)  # what track_call does on the way out
        assert llm_provider._provider_is_blocked("Mistral") == (False, None)


class TestCascadeSkipsDeadProviders:
    """End to end through the real `generate_completion` cascade."""

    def _wire(self, monkeypatch, outcomes):
        """Replace each provider with a stub; record the order they are tried."""
        tried = []

        def make(name):
            async def stub(system_prompt, user_prompt, max_tokens, temperature,
                           model=None, cached_content=None):
                tried.append(name)
                result = outcomes[name]
                if isinstance(result, Exception):
                    raise result
                return result, 10

            return stub

        for name, attr in [
            ("OpenAI", "_generate_openai"), ("Gemini", "_generate_gemini"),
            ("Groq", "_generate_groq"), ("Cerebras", "_generate_cerebras"),
            ("Mistral", "_generate_mistral"), ("Kimi", "_generate_kimi"),
            ("HuggingFace", "_generate_huggingface"),
        ]:
            if name in outcomes:
                monkeypatch.setattr(llm_provider, attr, make(name))
            elif name == "Kimi":
                async def _kimi_unused(*args, **kwargs):
                    raise RuntimeError("kimi not in this test")
                monkeypatch.setattr(llm_provider, attr, _kimi_unused)

        monkeypatch.setattr(llm_provider, "LLM_PROVIDER", "auto")
        monkeypatch.setattr(llm_provider.os, "getenv", lambda k, d=None: (
            "key" if k.endswith("_API_KEY") else (d if d is not None else "key")
        ))
        return tried

    @pytest.mark.anyio
    async def test_open_circuit_is_skipped(self, monkeypatch):
        tried = self._wire(monkeypatch, {
            "OpenAI": _http_error(401),
            "Gemini": "an answer",
            "Groq": "unused",
        })
        _trip("OpenAI")

        result = await llm_provider.generate_completion("sys", "user")

        assert result == "an answer"
        assert "OpenAI" not in tried, "a provider with an open circuit must not be called"
        assert tried[0] == "Gemini"

    @pytest.mark.anyio
    async def test_gate_stands_down_when_everything_is_open(self, monkeypatch):
        """A global blip must not leave the app refusing to try at all: with no
        healthy provider left, the cascade degrades to its old behaviour rather
        than answering nothing."""
        tried = self._wire(monkeypatch, {
            "OpenAI": _http_error(401),
            "Gemini": "recovered",
            "Groq": "unused",
        })
        for name in ("OpenAI", "Gemini", "Groq", "Cerebras", "Mistral", "Kimi", "HuggingFace"):
            _trip(name)

        result = await llm_provider.generate_completion("sys", "user")

        assert result == "recovered"
        assert tried, "every provider was skipped and nothing was attempted"

    @pytest.mark.anyio
    async def test_failure_names_what_was_skipped(self, monkeypatch):
        """A shortened cascade must not read like a total outage in the logs."""
        self._wire(monkeypatch, {
            "OpenAI": _http_error(401),
            "Gemini": _http_error(503),
            "Groq": _http_error(413),
        })
        _trip("OpenAI")

        with pytest.raises(RuntimeError) as excinfo:
            await llm_provider.generate_completion("sys", "user")

        assert "Skipped as unhealthy" in str(excinfo.value)
        assert "OpenAI" in str(excinfo.value)


class TestPerProviderInputBudget:
    """One prompt is built for the caller, not for whichever provider serves it.

    Groq returned `413 Payload Too Large` on every long paper and was skipped
    over, while being the only provider still alive. The cascade cannot rebuild
    the prompt, but it can cut it down to what the next provider accepts -- and
    *what* it cuts is the whole point: the instructions are at the top of the
    prompt and the user's question is at the bottom, so a plain truncation would
    hand the model a paper with no question attached.
    """

    def _prompt(self, paper_chars=44_000):
        from ai.pdf_analysis import _CUSTOM_PROMPT_TEMPLATE

        return (
            _CUSTOM_PROMPT_TEMPLATE
            .replace("{text}", "PAPER " * (paper_chars // 6))
            .replace("{custom_prompt}", "What is the main contribution?")
        )

    def test_a_large_window_provider_is_untouched(self):
        prompt = self._prompt()
        assert llm_provider._fit_to_provider(prompt, "Gemini") is prompt
        assert llm_provider._fit_to_provider(prompt, "OpenAI") is prompt

    def test_a_small_window_provider_is_trimmed_to_its_cap(self):
        prompt = self._prompt()
        sized = llm_provider._fit_to_provider(prompt, "Groq")
        assert len(sized) <= llm_provider._input_cap("Groq")
        assert len(sized) < len(prompt)

    def test_the_question_and_instructions_survive(self):
        """The failure this guards against is silent: the model would answer a
        paper it was never asked a question about."""
        prompt = self._prompt()
        sized = llm_provider._fit_to_provider(prompt, "HuggingFace")

        assert sized.startswith("You are an expert research assistant.")
        assert "What is the main contribution?" in sized
        assert "USER PROMPT:" in sized

    def test_only_the_document_is_cut(self):
        prompt = self._prompt()
        sized = llm_provider._fit_to_provider(prompt, "Groq")
        assert "<document>" in sized and "</document>" in sized
        body = sized[sized.index("<document>"):sized.index("</document>")]
        assert "trimmed to fit" in body

    def test_a_short_prompt_is_returned_unchanged(self):
        prompt = self._prompt(paper_chars=200)
        assert llm_provider._fit_to_provider(prompt, "Groq") is prompt

    def test_a_prompt_with_no_document_keeps_both_ends(self):
        """Not every caller wraps its input in <document> — a gap-analysis or
        manuscript prompt must still arrive with its head and tail intact."""
        prompt = "HEAD-INSTRUCTIONS " + ("filler " * 6000) + " TAIL-QUESTION"
        sized = llm_provider._fit_to_provider(prompt, "Groq")

        assert len(sized) <= llm_provider._input_cap("Groq")
        assert sized.startswith("HEAD-INSTRUCTIONS")
        assert sized.endswith("TAIL-QUESTION")

    def test_env_overrides_the_default_cap(self, monkeypatch):
        monkeypatch.setenv("LLM_INPUT_CHARS_GROQ", "5000")
        assert llm_provider._input_cap("Groq") == 5000
        assert len(llm_provider._fit_to_provider(self._prompt(), "Groq")) <= 5000

    @pytest.mark.parametrize("bad", ["", "nonsense", "12kb"])
    def test_an_unreadable_override_falls_back_to_the_default(self, monkeypatch, bad):
        monkeypatch.setenv("LLM_INPUT_CHARS_GROQ", bad)
        assert llm_provider._input_cap("Groq") == llm_provider._PROVIDER_INPUT_CHARS["groq"]

    @pytest.mark.parametrize("off", ["0", "-1"])
    def test_a_non_positive_override_switches_trimming_off(self, monkeypatch, off):
        """The escape hatch for a paid tier whose window the defaults understate."""
        monkeypatch.setenv("LLM_INPUT_CHARS_GROQ", off)
        assert llm_provider._input_cap("Groq") is None
        prompt = self._prompt()
        assert llm_provider._fit_to_provider(prompt, "Groq") is prompt


class TestProviderErrorsAreLegible:
    """`Client error '413 Payload Too Large'` names no limit and no number."""

    def test_the_response_body_is_included(self):
        request = httpx.Request("POST", "https://api.groq.test/v1/chat/completions")
        response = httpx.Response(
            413, request=request,
            json={"error": {"message": "Request too large for model `x` on tokens per "
                                       "minute (TPM): Limit 8000, Requested 10241."}},
        )
        exc = httpx.HTTPStatusError("413", request=request, response=response)

        described = llm_provider._describe_error(exc)
        assert "Limit 8000" in described
        assert "Requested 10241" in described

    def test_a_plain_exception_still_describes_itself(self):
        assert "boom" in llm_provider._describe_error(RuntimeError("boom"))

    def test_a_huge_body_is_clipped(self):
        request = httpx.Request("POST", "https://api.test/v1")
        response = httpx.Response(500, request=request, text="x" * 5000)
        exc = httpx.HTTPStatusError("500", request=request, response=response)
        assert len(llm_provider._describe_error(exc)) < 600
