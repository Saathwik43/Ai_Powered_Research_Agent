"""
Tests for the semantic result cache (Phase 2).

Embeddings are stubbed with hand-built vectors so similarity is exact and the
suite needs no API key. Real-world threshold tuning is a separate job — see
scripts/eval_semantic_cache.py.
"""

import asyncio
import math
import unittest
from unittest.mock import AsyncMock, patch

import integrations.paper_search as ps
from services import semantic_cache
from services.semantic_cache import (
    MODE_RERANK,
    MODE_VERBATIM,
    RERANK_THRESHOLD,
    VERBATIM_THRESHOLD,
)


def _unit_vector(angle_degrees: float) -> list:
    """2-D unit vector, so cosine similarity between two is cos(angle diff)."""
    radians = math.radians(angle_degrees)
    return [math.cos(radians), math.sin(radians)]


def _angle_for_similarity(target: float) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, target))))


def _papers(n: int, prefix: str = "Paper") -> list:
    return [
        {
            "title": f"{prefix} {i} on transformer attention",
            "abstract": f"Study {i}.",
            "doi": f"10.1/{prefix}-{i}",
            "source": "arXiv",
            "year": "2024",
            "citations": n - i,
        }
        for i in range(n)
    ]


class TestSemanticCacheStore(unittest.TestCase):
    def setUp(self):
        semantic_cache.clear()

    def test_miss_on_empty_cache(self):
        self.assertIsNone(semantic_cache.lookup("b", "k", _unit_vector(0)))

    def test_verbatim_tier(self):
        semantic_cache.store("b", "stored", _unit_vector(0), "machine learning", _papers(3))
        angle = _angle_for_similarity(VERBATIM_THRESHOLD + 0.005)
        hit = semantic_cache.lookup("b", "other", _unit_vector(angle))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.mode, MODE_VERBATIM)
        self.assertFalse(hit.needs_rerank)
        self.assertEqual(hit.matched_query, "machine learning")

    def test_rerank_tier(self):
        semantic_cache.store("b", "stored", _unit_vector(0), "machine learning", _papers(3))
        angle = _angle_for_similarity((RERANK_THRESHOLD + VERBATIM_THRESHOLD) / 2)
        hit = semantic_cache.lookup("b", "other", _unit_vector(angle))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.mode, MODE_RERANK)
        self.assertTrue(hit.needs_rerank)

    def test_below_threshold_is_a_miss(self):
        semantic_cache.store("b", "stored", _unit_vector(0), "machine learning", _papers(3))
        angle = _angle_for_similarity(RERANK_THRESHOLD - 0.02)
        self.assertIsNone(semantic_cache.lookup("b", "other", _unit_vector(angle)))

    def test_own_key_is_skipped(self):
        """An exact hit is the exact cache's job; matching a query against
        itself would report a bogus semantic hit."""
        semantic_cache.store("b", "same-key", _unit_vector(0), "machine learning", _papers(3))
        self.assertIsNone(semantic_cache.lookup("b", "same-key", _unit_vector(0)))

    def test_buckets_are_isolated(self):
        """A limit=5 search must never be served from a limit=50 entry."""
        semantic_cache.store("bucket-a", "stored", _unit_vector(0), "machine learning", _papers(3))
        self.assertIsNone(semantic_cache.lookup("bucket-b", "other", _unit_vector(0)))

    def test_closest_entry_wins(self):
        semantic_cache.store("b", "far", _unit_vector(0), "far query", _papers(1, "Far"))
        semantic_cache.store("b", "near", _unit_vector(1.0), "near query", _papers(1, "Near"))
        hit = semantic_cache.lookup("b", "probe", _unit_vector(1.0))
        # 'near' is identical to the probe; 'far' is 1 degree off.
        self.assertEqual(hit.matched_query, "near query")

    def test_hit_does_not_expose_the_stored_list(self):
        stored = _papers(3)
        semantic_cache.store("b", "stored", _unit_vector(0), "machine learning", stored)
        hit = semantic_cache.lookup("b", "other", _unit_vector(0))
        hit.papers[0]["title"] = "MUTATED"
        hit.papers.pop()
        again = semantic_cache.lookup("b", "other", _unit_vector(0))
        self.assertEqual(len(again.papers), 3)
        self.assertNotEqual(again.papers[0]["title"], "MUTATED")

    def test_expired_entries_are_pruned(self):
        semantic_cache.store("b", "stored", _unit_vector(0), "machine learning", _papers(3))
        bucket = semantic_cache._store["b"]
        embedding, papers, query, _stored_at = bucket["stored"][:4]
        bucket["stored"] = (embedding, papers, query, 0.0)  # epoch = long expired
        self.assertIsNone(semantic_cache.lookup("b", "other", _unit_vector(0)))

    def test_capacity_is_bounded(self):
        for i in range(semantic_cache.MAX_ENTRIES + 25):
            semantic_cache.store("b", f"k{i}", _unit_vector(i * 0.001), f"q{i}", _papers(1))
        self.assertLessEqual(len(semantic_cache._store["b"]), semantic_cache.MAX_ENTRIES)

    def test_store_without_embedding_is_a_noop(self):
        semantic_cache.store("b", "stored", None, "machine learning", _papers(3))
        semantic_cache.store("b", "stored", [], "machine learning", _papers(3))
        self.assertIsNone(semantic_cache.lookup("b", "other", _unit_vector(0)))


class TestSearchAllIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        semantic_cache.clear()
        ps._cache.clear()
        ps._embedding_cache.clear()

    async def _search(self, query, embedding, fan_out_result, **kwargs):
        """Run search_all_with_meta with the query embedding and fan-out stubbed."""
        with patch.object(ps, "_query_embedding", AsyncMock(return_value=embedding)), \
             patch.object(ps, "_execute_search", AsyncMock(return_value=fan_out_result)) as fan_out:
            papers, meta = await ps.search_all_with_meta(query, **kwargs)
        return papers, meta, fan_out

    async def test_exact_hit_reports_exact_and_skips_fan_out(self):
        papers = _papers(3)
        await self._search("machine learning", _unit_vector(0), papers)
        # Seed the exact cache the way _execute_search would.
        key = f"{ps.canonical_key('machine learning')}_20_True_all"
        ps._cache[key] = (papers, ps.time.time())

        got, meta, fan_out = await self._search(
            "Machine  Learning", _unit_vector(0), [], limit_per_source=20
        )
        self.assertEqual(meta.cache, "exact")
        self.assertIsNone(meta.matched_query)
        fan_out.assert_not_awaited()

    async def test_semantic_hit_avoids_the_fan_out_and_reports_the_match(self):
        stored = _papers(4)
        _, meta, fan_out = await self._search("machine learning in healthcare", _unit_vector(0), stored)
        self.assertEqual(meta.cache, "miss")
        # _execute_search is stubbed, so store the entry the way it would.
        semantic_cache.store(
            "15_True", "stored-key", _unit_vector(0), "machine learning in healthcare", stored
        )

        angle = _angle_for_similarity(VERBATIM_THRESHOLD + 0.005)
        papers, meta, fan_out = await self._search("ML in healthcare", _unit_vector(angle), [])
        self.assertEqual(meta.cache, "semantic")
        self.assertEqual(meta.matched_query, "machine learning in healthcare")
        self.assertEqual(len(papers), 4)
        fan_out.assert_not_awaited()

    async def test_rerank_tier_reorders_for_the_typed_query(self):
        """Below the verbatim threshold only the candidate pool is trusted, so
        the result must be re-ranked against the query the user typed."""
        stored = [
            {"title": "Quantum annealing hardware", "abstract": "annealing", "doi": "10.1/a",
             "source": "arXiv", "year": "2024", "citations": 100},
            {"title": "Photonic circuits for computing", "abstract": "photonics", "doi": "10.1/b",
             "source": "arXiv", "year": "2024", "citations": 0},
        ]
        semantic_cache.store("15_True", "stored-key", _unit_vector(0), "quantum annealing", stored)

        angle = _angle_for_similarity((RERANK_THRESHOLD + VERBATIM_THRESHOLD) / 2)
        with patch.object(ps, "_embed_papers_cached", AsyncMock(return_value=[None, None])):
            papers, meta, fan_out = await self._search("photonic circuits", _unit_vector(angle), [])

        self.assertEqual(meta.cache, "semantic")
        fan_out.assert_not_awaited()
        # Lexical match now favours the photonics paper despite zero citations.
        self.assertEqual(papers[0]["title"], "Photonic circuits for computing")

    async def test_allow_semantic_cache_false_forces_a_real_search(self):
        stored = _papers(4)
        semantic_cache.store("15_True", "stored-key", _unit_vector(0), "machine learning", stored)

        angle = _angle_for_similarity(VERBATIM_THRESHOLD + 0.005)
        _, meta, fan_out = await self._search(
            "ML", _unit_vector(angle), _papers(2), allow_semantic_cache=False
        )
        self.assertEqual(meta.cache, "miss")
        self.assertIsNone(meta.matched_query)
        fan_out.assert_awaited_once()

    async def test_no_embedding_disables_the_semantic_path(self):
        semantic_cache.store("15_True", "stored-key", _unit_vector(0), "machine learning", _papers(4))
        _, meta, fan_out = await self._search("ML", None, _papers(2))
        self.assertEqual(meta.cache, "miss")
        fan_out.assert_awaited_once()

    async def test_semantic_rerank_off_skips_embeddings_entirely(self):
        with patch.object(ps, "_query_embedding", AsyncMock()) as embed, \
             patch.object(ps, "_execute_search", AsyncMock(return_value=_papers(2))):
            _, meta = await ps.search_all_with_meta("machine learning", semantic_rerank=False)
        embed.assert_not_awaited()
        self.assertEqual(meta.cache, "miss")

    async def test_concurrent_identical_searches_still_share_one_fan_out(self):
        """
        Single-flight must cover the embedding lookup too.

        Fetching the query embedding before registering the in-flight task
        gave two concurrent identical searches a window in which both saw an
        empty _inflight and both fanned out to all 11 sources.
        """
        ps._inflight.clear()

        async def slow_embedding(_query):
            await asyncio.sleep(0.05)
            return _unit_vector(0)

        async def slow_fan_out(*args, **kwargs):
            await asyncio.sleep(0.05)
            return _papers(3)

        with patch.object(ps, "_query_embedding", side_effect=slow_embedding), \
             patch.object(ps, "_execute_search", side_effect=slow_fan_out) as fan_out:
            results = await asyncio.gather(
                ps.search_all_with_meta("transformer attention"),
                ps.search_all_with_meta("transformer attention"),
            )

        self.assertEqual(fan_out.call_count, 1)
        self.assertEqual(results[0][0], results[1][0])

    async def test_fresh_request_is_not_served_by_a_concurrent_cached_one(self):
        """allow_semantic_cache=False must not be collapsed into a sibling
        request that is allowed to use the cache."""
        ps._inflight.clear()

        async def slow_fan_out(*args, **kwargs):
            await asyncio.sleep(0.05)
            return _papers(3)

        with patch.object(ps, "_query_embedding", AsyncMock(return_value=_unit_vector(0))), \
             patch.object(ps, "_execute_search", side_effect=slow_fan_out) as fan_out:
            await asyncio.gather(
                ps.search_all_with_meta("transformer attention", allow_semantic_cache=True),
                ps.search_all_with_meta("transformer attention", allow_semantic_cache=False),
            )

        self.assertEqual(fan_out.call_count, 2)

    async def test_search_all_still_returns_a_plain_list(self):
        with patch.object(ps, "_query_embedding", AsyncMock(return_value=None)), \
             patch.object(ps, "_execute_search", AsyncMock(return_value=_papers(3))):
            result = await ps.search_all("machine learning")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()
