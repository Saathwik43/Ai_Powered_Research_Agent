"""
test_search_efficiency.py
-------------------------
Regression tests for the cost of a single Dashboard search.

The search path is expensive in two ways: the relevance classifier costs one
LLM call per paper, and semantic reranking costs one embedding request per
paper. These tests pin down the fixes that bound both:

* GET /api/literature classifies a window proportional to `limit` — enough to
  backfill past irrelevant papers, never the whole corpus.
* Embeddings for a rerank window are fetched in batched requests.
* Topic discovery reuses the literature endpoint's search_all cache entry
  and never invokes the per-paper classifier.
"""

import asyncio
import math
import re

import pytest
from unittest.mock import AsyncMock, patch

import ai.llm_provider as llm_provider
import ai.relevance as relevance_module
import ai.topic_discovery as topic_discovery
import integrations.paper_search as paper_search
from ai.topic_discovery import TOPIC_CORPUS_SIZE, discover_topics
from fastapi.testclient import TestClient
from integrations.paper_search import SHARED_LIMIT_PER_SOURCE
from integrations.paper_search import SearchMeta

def _searched(papers):
    """
    Return value for a patched routers.discovery.search_all_with_meta.

    The endpoint calls search_all_with_meta (it needs the semantic-cache
    disclosure), so that is the name tests must patch. Patching plain
    search_all silently stops intercepting and the test hits the network.
    """
    return (papers, SearchMeta(cache="miss"))

from main import app
from core.auth import get_current_user
from routers.discovery import LITERATURE_MAX_LIMIT, _BACKFILL_HEADROOM
from ai.relevance import BATCH_SIZE

_BATCH_ENTRY_RE = re.compile(r"^(\d+)\. Title: ", re.MULTILINE)


def _batch_classifier(system_prompt, user_prompt, *args, **kwargs):
    """
    Answer a batched classifier prompt in the shape the parser expects.

    Returning a bare "yes" would fail to parse and silently trigger the
    per-paper fallback, so the test would measure the fallback rather than the
    batching it is meant to pin down.
    """
    numbers = _BATCH_ENTRY_RE.findall(user_prompt)
    if numbers:
        return "\n".join(f"{n}: yes" for n in numbers)
    return "yes"



app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
app.state.limiter.enabled = False
client = TestClient(app)


def _papers(n: int, source: str = "arXiv", prefix: str = "Paper"):
    return [
        {
            "title": f"{prefix} {i} on transformer attention mechanisms",
            "abstract": f"Transformer attention optimization study number {i}.",
            "source": source,
            "url": f"https://example.com/{prefix}-{i}",
        }
        for i in range(n)
    ]


# ─── GET /api/literature: limit bounds the classifier ──────────────────────────

class TestLiteratureLimit:

    def setup_method(self):
        relevance_module._relevance_cache.clear()

    def _get(self, params):
        with patch("routers.discovery.search_all_with_meta", new_callable=AsyncMock) as mock_search, \
             patch("ai.relevance.generate_completion", new_callable=AsyncMock) as mock_gen:
            mock_search.return_value = _searched(_papers(40))
            mock_gen.side_effect = _batch_classifier
            resp = client.get("/api/literature", params=params)
        return resp, mock_gen

    def test_classifier_cost_is_proportional_to_the_request(self):
        """
        40 papers found, 5 requested → one backfill round, not 40 calls.

        The round is `limit + _BACKFILL_HEADROOM` so the request can still be
        filled when the ranked head is mostly irrelevant. Every paper here is
        relevant, so one round is enough and the extra headroom is the entire
        overhead.
        """
        resp, mock_gen = self._get({"query": "transformer attention", "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        examined = 5 + _BACKFILL_HEADROOM
        assert mock_gen.call_count == math.ceil(examined / BATCH_SIZE)
        assert mock_gen.call_count < examined, "papers must be judged a page at a time"
        assert body["count"] == 5
        assert body["total"] == 40
        assert body["has_more"] is True

    def test_has_more_false_when_window_covers_everything(self):
        resp, _ = self._get({"query": "transformer attention", "limit": 40})
        body = resp.json()
        assert body["has_more"] is False
        assert body["limit"] == 40

    def test_limit_is_clamped_to_ceiling(self):
        resp, mock_gen = self._get({"query": "transformer attention", "limit": 10_000})
        assert resp.json()["limit"] == LITERATURE_MAX_LIMIT
        # Fixture is smaller than the ceiling, so every paper is classified once
        # — in pages of BATCH_SIZE.
        assert mock_gen.call_count == math.ceil(40 / BATCH_SIZE)

    def test_limit_is_clamped_to_floor(self):
        resp, mock_gen = self._get({"query": "transformer attention", "limit": 0})
        body = resp.json()
        assert body["limit"] == 1
        assert body["count"] == 1
        assert mock_gen.call_count == math.ceil((1 + _BACKFILL_HEADROOM) / BATCH_SIZE)

    def test_negative_limit_does_not_empty_the_window(self):
        resp, mock_gen = self._get({"query": "transformer attention", "limit": -5})
        body = resp.json()
        assert body["limit"] == 1
        assert body["count"] == 1
        assert mock_gen.call_count == math.ceil((1 + _BACKFILL_HEADROOM) / BATCH_SIZE)


# ─── Batched embeddings ───────────────────────────────────────────────────────

class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeResponse:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeModels:
    """Records every embed_content request so batching is observable."""

    def __init__(self, fail_on=(), short_by=0):
        self.calls = []
        self._fail_on = set(fail_on)
        self._short_by = short_by

    async def embed_content(self, model=None, contents=None, config=None):
        index = len(self.calls)
        self.calls.append(list(contents))
        if index in self._fail_on:
            raise RuntimeError("embedding backend unavailable")
        wanted = len(contents) - self._short_by
        return _FakeResponse([_FakeEmbedding([float(index), 1.0]) for _ in range(wanted)])


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeGeminiClient:
    def __init__(self, models):
        self.aio = _FakeAio(models)


@pytest.fixture
def fake_gemini(monkeypatch):
    def _install(**kwargs):
        models = _FakeModels(**kwargs)
        monkeypatch.setattr(llm_provider, "_gemini_client", _FakeGeminiClient(models))
        return models
    return _install


class TestGetEmbeddingsBatch:

    def test_one_request_per_chunk(self, fake_gemini):
        models = fake_gemini()
        texts = [f"text {i}" for i in range(120)]

        result = asyncio.run(llm_provider.get_embeddings_batch(texts))

        assert [len(c) for c in models.calls] == [50, 50, 20]
        assert len(result) == 120
        assert all(isinstance(v, list) for v in result)

    def test_window_of_thirty_is_a_single_request(self, fake_gemini):
        models = fake_gemini()
        asyncio.run(llm_provider.get_embeddings_batch([f"text {i}" for i in range(30)]))
        assert len(models.calls) == 1

    def test_failing_chunk_only_nulls_its_own_texts(self, fake_gemini):
        models = fake_gemini(fail_on=(1,))
        texts = [f"text {i}" for i in range(120)]

        result = asyncio.run(llm_provider.get_embeddings_batch(texts))

        assert len(models.calls) == 3
        assert all(v is not None for v in result[:50])
        assert all(v is None for v in result[50:100])
        assert all(v is not None for v in result[100:])

    def test_short_response_keeps_positional_alignment(self, fake_gemini):
        fake_gemini(short_by=3)
        result = asyncio.run(llm_provider.get_embeddings_batch([f"text {i}" for i in range(10)]))
        assert len(result) == 10
        assert result[-3:] == [None, None, None]

    def test_empty_input_makes_no_request(self, fake_gemini):
        models = fake_gemini()
        assert asyncio.run(llm_provider.get_embeddings_batch([])) == []
        assert models.calls == []

    def test_missing_api_key_returns_aligned_nones(self, monkeypatch):
        monkeypatch.setattr(llm_provider, "_gemini_client", None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert asyncio.run(llm_provider.get_embeddings_batch(["a", "b"])) == [None, None]

    def test_single_embedding_delegates_to_batch(self, fake_gemini):
        models = fake_gemini()
        value = asyncio.run(llm_provider.get_embedding("one text"))
        assert models.calls == [["one text"]]
        assert isinstance(value, list)


class TestEmbedPapersCached:

    def setup_method(self):
        paper_search._embedding_cache.clear()

    def test_window_costs_one_batched_call_then_zero(self):
        papers = _papers(30)
        with patch("ai.llm_provider.get_embeddings_batch", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = [[1.0, 0.0]] * 30
            first = asyncio.run(paper_search._embed_papers_cached(papers))
            assert mock_batch.call_count == 1
            assert len(mock_batch.call_args.args[0]) == 30

            second = asyncio.run(paper_search._embed_papers_cached(papers))
            assert mock_batch.call_count == 1, "cached window must not re-request"

        assert first == second

    def test_only_cache_misses_are_requested(self):
        papers = _papers(4)
        with patch("ai.llm_provider.get_embeddings_batch", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = [[1.0, 0.0]] * 4
            asyncio.run(paper_search._embed_papers_cached(papers))

            mock_batch.reset_mock()
            mock_batch.return_value = [[2.0, 0.0]] * 2
            extended = papers + _papers(2, prefix="Extra")
            result = asyncio.run(paper_search._embed_papers_cached(extended))

            assert len(mock_batch.call_args.args[0]) == 2
        assert result[:4] == [[1.0, 0.0]] * 4
        assert result[4:] == [[2.0, 0.0]] * 2

    def test_failures_are_cached_briefly_and_stay_aligned(self):
        papers = _papers(3)
        with patch("ai.llm_provider.get_embeddings_batch", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = [[1.0, 0.0], None, [3.0, 0.0]]
            result = asyncio.run(paper_search._embed_papers_cached(papers))
        assert result[1] is None

        ttls = [expires for _, expires in paper_search._embedding_cache.values()]
        # A failed embedding expires an order of magnitude sooner than a good one.
        assert max(ttls) - min(ttls) == pytest.approx(540, abs=2)


# ─── Topic discovery shares one fan-out ───────────────────────────────────────

class TestTopicDiscoveryFanOut:

    def test_search_args_match_the_literature_endpoint(self):
        with patch("ai.topic_discovery.search_all", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = _papers(20)
            asyncio.run(discover_topics("transformer attention mechanisms"))

        assert mock_search.call_count == 1
        assert mock_search.call_args.kwargs == {"limit_per_source": SHARED_LIMIT_PER_SOURCE}

    def test_no_per_paper_classifier_in_the_topics_path(self):
        assert not hasattr(topic_discovery, "_filter_relevant_papers")

    def test_grey_literature_is_dropped_after_the_shared_search(self):
        # BASE is no longer a fan-out source, so DOAJ is the only remaining
        # broad-OA source topic discovery filters out.
        corpus = _papers(5, source="OpenAlex", prefix="Kept") + \
            _papers(5, source="DOAJ", prefix="Broad")

        with patch("ai.topic_discovery.search_all", new_callable=AsyncMock) as mock_search, \
             patch("ai.topic_discovery.extract_top_topics") as mock_extract:
            mock_search.return_value = corpus
            mock_extract.return_value = [{"id": 1, "title": "attention", "impact": "High"}]
            asyncio.run(discover_topics("transformer attention mechanisms"))

        docs = mock_extract.call_args.args[0]
        assert len(docs) == 5
        assert all("Kept" in d for d in docs)

    def test_parallel_identical_searches_share_one_fan_out(self):
        paper_search._cache.clear()
        paper_search._inflight.clear()

        async def slow_search(*args, **kwargs):
            await asyncio.sleep(0.05)
            return _papers(3)

        async def two_parallel_searches():
            with patch.object(paper_search, "_execute_search", side_effect=slow_search) as mock_exec:
                results = await asyncio.gather(
                    paper_search.search_all("transformer attention", limit_per_source=SHARED_LIMIT_PER_SOURCE),
                    paper_search.search_all("transformer attention", limit_per_source=SHARED_LIMIT_PER_SOURCE),
                )
            return mock_exec.call_count, results

        call_count, results = asyncio.run(two_parallel_searches())
        assert call_count == 1
        assert results[0] == results[1]

    def test_corpus_is_bounded(self):
        with patch("ai.topic_discovery.search_all", new_callable=AsyncMock) as mock_search, \
             patch("ai.topic_discovery.extract_top_topics") as mock_extract:
            mock_search.return_value = _papers(TOPIC_CORPUS_SIZE + 40)
            mock_extract.return_value = [{"id": 1, "title": "attention", "impact": "High"}]
            asyncio.run(discover_topics("transformer attention mechanisms"))

        assert len(mock_extract.call_args.args[0]) == TOPIC_CORPUS_SIZE
