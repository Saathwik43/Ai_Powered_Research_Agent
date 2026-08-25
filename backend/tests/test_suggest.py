"""
Tests for /api/suggest and the query-history store (Phase 5).

The point of this endpoint is that acronyms work. The old client-side
`SUGGESTIONS.filter(s => s.includes(q))` returned nothing for "CNN", "ML" or
"NAS" because no entry contained those literal substrings.
"""

import unittest

from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app
from services import query_history
from services.query_history import _suggest_rank, record_query, recent_queries

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
app.state.limiter.enabled = False
client = TestClient(app)


class TestSuggestRanking(unittest.TestCase):
    def test_whole_phrase_prefix_ranks_first(self):
        self.assertEqual(_suggest_rank("mac", "machine learning in healthcare"), 0)

    def test_word_prefix(self):
        self.assertEqual(_suggest_rank("learn", "machine learning"), 1)

    def test_initials_match(self):
        """The case plain substring matching could never handle."""
        self.assertEqual(_suggest_rank("nas", "neural architecture search"), 2)
        self.assertEqual(_suggest_rank("cnn", "convolutional neural networks"), 2)
        self.assertEqual(_suggest_rank("gnn", "graph neural networks"), 2)

    def test_substring_is_the_last_resort(self):
        self.assertEqual(_suggest_rank("hine", "machine learning"), 3)

    def test_no_match(self):
        self.assertIsNone(_suggest_rank("zzz", "machine learning"))

    def test_single_letter_does_not_trigger_initials(self):
        """One letter would match nearly every phrase's first initial."""
        self.assertNotEqual(_suggest_rank("m", "machine learning"), 2)

    def test_stronger_match_outranks_weaker(self):
        prefix = "gen"
        self.assertLess(
            _suggest_rank(prefix, "generative AI"),          # phrase prefix
            _suggest_rank(prefix, "protein gene editing"),   # word prefix
        )


class TestQueryHistory(unittest.TestCase):
    def setUp(self):
        query_history.clear()

    def test_most_recent_first(self):
        record_query("alpha")
        record_query("beta")
        self.assertEqual(recent_queries()[0], "beta")

    def test_case_variants_collapse(self):
        record_query("Deep Learning")
        record_query("deep learning")
        self.assertEqual(len(recent_queries()), 1)

    def test_whitespace_is_normalised(self):
        record_query("  deep   learning  ")
        self.assertEqual(recent_queries(), ["deep learning"])

    def test_blank_and_overlong_queries_are_ignored(self):
        record_query("")
        record_query("   ")
        record_query(None)
        record_query("x" * 500)
        self.assertEqual(recent_queries(), [])

    def test_capacity_is_bounded(self):
        for i in range(400):
            record_query(f"query number {i}")
        self.assertLessEqual(len(recent_queries()), 300)


class TestSuggestEndpoint(unittest.TestCase):
    def setUp(self):
        query_history.clear()

    def test_empty_query_returns_seed_suggestions(self):
        body = client.get("/api/suggest").json()
        self.assertTrue(body["data"])
        self.assertLessEqual(len(body["data"]), 8)

    def test_acronym_finds_the_expansion(self):
        body = client.get("/api/suggest", params={"q": "nas"}).json()
        self.assertIn("neural architecture search", body["data"])

    def test_prefix_match(self):
        body = client.get("/api/suggest", params={"q": "quan"}).json()
        self.assertIn("quantum computing", body["data"])

    def test_recorded_queries_are_suggested(self):
        record_query("ferroelectric nematic liquid crystals")
        body = client.get("/api/suggest", params={"q": "ferro"}).json()
        self.assertIn("ferroelectric nematic liquid crystals", body["data"])

    def test_recorded_queries_outrank_seeds_at_the_same_tier(self):
        record_query("machine learning operations")
        body = client.get("/api/suggest", params={"q": "machine learning o"}).json()
        self.assertEqual(body["data"][0], "machine learning operations")

    def test_no_duplicates(self):
        record_query("quantum computing")  # already a seed
        body = client.get("/api/suggest", params={"q": "quantum"}).json()
        self.assertEqual(len(body["data"]), len(set(body["data"])))

    def test_unmatched_prefix_returns_nothing(self):
        body = client.get("/api/suggest", params={"q": "zzzzqqqq"}).json()
        self.assertEqual(body["data"], [])

    def test_result_count_is_capped(self):
        for i in range(50):
            record_query(f"machine learning topic {i}")
        body = client.get("/api/suggest", params={"q": "machine"}).json()
        self.assertLessEqual(len(body["data"]), 8)


if __name__ == "__main__":
    unittest.main()
