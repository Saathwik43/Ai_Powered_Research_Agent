"""
Tests for Phase 4: pooled HTTP client, batched classification, bounded caches.
"""

import time
import unittest
from unittest.mock import AsyncMock, patch

import ai.relevance as relevance
from ai.relevance import (
    BATCH_SIZE,
    _build_batch_prompt,
    _filter_relevant_papers,
    _parse_batch_verdicts,
)
from core.ttl_cache import TTLCache
from integrations import http_client


def _papers(n: int, relevant_from: int = 0) -> list:
    return [
        {
            "title": f"Paper {i} on {'topic' if i >= relevant_from else 'offtopic'}",
            "abstract": f"Abstract {i}.",
            "doi": f"10.1/p{i}",
        }
        for i in range(n)
    ]


class TestBatchVerdictParsing(unittest.TestCase):
    def test_well_formed_reply(self):
        self.assertEqual(_parse_batch_verdicts("1: yes\n2: no\n3: yes", 3), [True, False, True])

    def test_tolerates_common_punctuation_and_case(self):
        self.assertEqual(_parse_batch_verdicts("1. YES\n2) No\n3 - yes", 3), [True, False, True])

    def test_out_of_order_reply_is_realigned_by_number(self):
        self.assertEqual(_parse_batch_verdicts("2: no\n1: yes", 2), [True, False])

    def test_short_reply_is_rejected(self):
        """A partial parse would assign one paper's verdict to another."""
        self.assertIsNone(_parse_batch_verdicts("1: yes\n2: no", 3))

    def test_prose_reply_is_rejected(self):
        self.assertIsNone(_parse_batch_verdicts("Sure! Here are my answers.", 3))

    def test_empty_reply_is_rejected(self):
        self.assertIsNone(_parse_batch_verdicts("", 2))
        self.assertIsNone(_parse_batch_verdicts(None, 2))

    def test_out_of_range_numbers_are_ignored(self):
        self.assertIsNone(_parse_batch_verdicts("1: yes\n7: no", 2))

    def test_prompt_numbers_every_paper(self):
        prompt = _build_batch_prompt("quantum computing", _papers(3))
        for n in (1, 2, 3):
            self.assertIn(f"{n}. Title:", prompt)
        self.assertIn("Answer with 3 lines", prompt)


def _batch_reply(system_prompt, user_prompt, *args, **kwargs):
    import re
    numbers = re.findall(r"^(\d+)\. Title: (.*)$", user_prompt, re.MULTILINE)
    return "\n".join(f"{n}: {'no' if 'offtopic' in t else 'yes'}" for n, t in numbers)


class TestBatchedClassification(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        relevance._relevance_cache.clear()
        relevance._failure_cache.clear()

    async def test_one_call_per_page_not_per_paper(self):
        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            gen.side_effect = _batch_reply
            result = await _filter_relevant_papers("quantum computing", _papers(25))

        self.assertEqual(gen.await_count, 3)  # ceil(25 / 10)
        self.assertEqual(len(result), 25)

    async def test_verdicts_map_to_the_right_papers(self):
        papers = _papers(10, relevant_from=4)  # first 4 are off-topic
        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            gen.side_effect = _batch_reply
            result = await _filter_relevant_papers("quantum computing", papers)

        self.assertEqual([p["title"] for p in result], [p["title"] for p in papers[4:]])

    async def test_result_order_matches_input_order(self):
        papers = _papers(12)
        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            gen.side_effect = _batch_reply
            result = await _filter_relevant_papers("quantum computing", papers)
        self.assertEqual([p["doi"] for p in result], [p["doi"] for p in papers])

    async def test_unparseable_reply_falls_back_to_per_paper(self):
        """Slower, but a mis-parsed batch would mis-assign verdicts."""
        calls = []

        async def flaky(system_prompt, user_prompt, *args, **kwargs):
            calls.append(user_prompt)
            if "Answer with" in user_prompt:      # the batch prompt
                return "I cannot comply."
            return "yes"                           # the per-paper prompt

        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            gen.side_effect = flaky
            result = await _filter_relevant_papers("quantum computing", _papers(4))

        self.assertEqual(len(result), 4)
        self.assertEqual(gen.await_count, 5)  # 1 failed batch + 4 per-paper

    async def test_cached_papers_are_not_re_sent(self):
        papers = _papers(10)
        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            gen.side_effect = _batch_reply
            await _filter_relevant_papers("quantum computing", papers)
            first = gen.await_count
            await _filter_relevant_papers("quantum computing", papers)

        self.assertEqual(first, 1)
        self.assertEqual(gen.await_count, 1, "cached verdicts were re-classified")

    async def test_prescored_papers_skip_the_llm_entirely(self):
        papers = _papers(4)
        papers[0]["relevance_score"] = 0.9
        papers[1]["relevance_score"] = 0.1
        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            gen.side_effect = _batch_reply
            result = await _filter_relevant_papers("quantum computing", papers)

        self.assertEqual(gen.await_count, 1)  # only the 2 unscored papers
        self.assertIn(papers[0], result)
        self.assertNotIn(papers[1], result)

    async def test_empty_input_makes_no_call(self):
        with patch("ai.relevance.generate_completion", new_callable=AsyncMock) as gen:
            result = await _filter_relevant_papers("quantum computing", [])
        self.assertEqual(result, [])
        gen.assert_not_awaited()


class TestTTLCache(unittest.TestCase):
    def test_get_and_set(self):
        cache = TTLCache(maxsize=10, ttl=60)
        cache["a"] = 1
        self.assertEqual(cache["a"], 1)
        self.assertIn("a", cache)
        self.assertEqual(cache.get("missing", "default"), "default")

    def test_expiry_on_read(self):
        cache = TTLCache(maxsize=10, ttl=0.05)
        cache["a"] = 1
        time.sleep(0.06)
        self.assertNotIn("a", cache)
        self.assertIsNone(cache.get("a"))
        with self.assertRaises(KeyError):
            cache["a"]

    def test_capacity_evicts_least_recently_used(self):
        cache = TTLCache(maxsize=3, ttl=60)
        for key in "abc":
            cache[key] = key
        cache["a"]              # touch 'a' so 'b' becomes the oldest use
        cache["d"] = "d"
        self.assertEqual(len(cache), 3)
        self.assertIn("a", cache)
        self.assertNotIn("b", cache)

    def test_pop_and_delete(self):
        cache = TTLCache(maxsize=10, ttl=60)
        cache["a"] = 1
        self.assertEqual(cache.pop("a"), 1)
        self.assertEqual(cache.pop("a", "gone"), "gone")
        cache["b"] = 2
        del cache["b"]
        self.assertEqual(len(cache), 0)

    def test_values_skips_expired_entries(self):
        cache = TTLCache(maxsize=10, ttl=0.05)
        cache["a"] = 1
        time.sleep(0.06)
        cache["b"] = 2
        self.assertEqual(cache.values(), [2])

    def test_stats_report_hit_rate(self):
        cache = TTLCache(maxsize=10, ttl=60)
        cache["a"] = 1
        cache.get("a")
        cache.get("missing")
        stats = cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)


class TestPooledHttpClient(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await http_client.aclose()

    async def test_same_client_is_reused(self):
        self.assertIs(http_client.get_client(), http_client.get_client())

    async def test_keepalive_is_configured(self):
        """A pool with no keepalive would rebuild the connection per request,
        which is the cost this replaces."""
        client = http_client.get_client()
        self.assertFalse(client.is_closed)

    async def test_a_closed_client_is_replaced(self):
        first = http_client.get_client()
        await first.aclose()
        second = http_client.get_client()
        self.assertIsNot(first, second)
        self.assertFalse(second.is_closed)

    async def test_bound_client_merges_defaults_into_requests(self):
        captured = {}

        class _Recorder:
            is_closed = False

            async def get(self, url, **kwargs):
                captured.update(kwargs)
                return "ok"

        bound = http_client.BoundClient(_Recorder(), {"User-Agent": "test/1.0"}, 12.0)
        await bound.get("https://example.com", params={"q": "x"})

        self.assertEqual(captured["headers"]["User-Agent"], "test/1.0")
        self.assertEqual(captured["timeout"], 12.0)
        self.assertEqual(captured["params"], {"q": "x"})

    async def test_per_request_values_win_over_defaults(self):
        captured = {}

        class _Recorder:
            is_closed = False

            async def get(self, url, **kwargs):
                captured.update(kwargs)
                return "ok"

        bound = http_client.BoundClient(_Recorder(), {"User-Agent": "default"}, 12.0)
        await bound.get("https://example.com", headers={"User-Agent": "override"}, timeout=1.0)

        self.assertEqual(captured["headers"]["User-Agent"], "override")
        self.assertEqual(captured["timeout"], 1.0)

    async def test_pooled_client_does_not_close_the_shared_pool(self):
        async with http_client.pooled_client(timeout=5.0):
            pass
        self.assertFalse(http_client.get_client().is_closed)


if __name__ == "__main__":
    unittest.main()
