"""
Tests for canonical query identity (core/query_key.py).

These pin the contract the caches depend on: queries that mean the same thing
share a key, queries that mean different things do not, and `display` is never
mangled.
"""

import pytest

from core.query_key import canonical, canonical_key


class TestSameQueryOneKey:
    """Variants a user would consider the same search must collapse."""

    @pytest.mark.parametrize("a, b, why", [
        ("machine learning", "Machine Learning", "case"),
        ("machine learning", "  machine   learning  ", "whitespace"),
        ("machine learning", "MACHINE LEARNING", "upper"),
        ("machine learning", "machine-learning", "punctuation"),
        ("neural network", "neural networks", "plural"),
        ("machine learning", "learning machine", "word order"),
        ("deep learning for NLP", "deep learning NLP", "grammar word"),
        # The Dashboard's Title-Cased direction label vs the raw phrase.
        ("sparse attention", "Sparse Attention", "topic label round-trip"),
    ])
    def test_collapses(self, a, b, why):
        assert canonical_key(a) == canonical_key(b), f"should collapse on {why}"


class TestDifferentQueryDifferentKey:
    """Canonicalisation must not merge genuinely different searches."""

    @pytest.mark.parametrize("a, b", [
        ("machine learning", "deep learning"),
        ("quantum computing", "quantum cryptography"),
        ("CNN classification", "RNN classification"),
        # One-letter tokens carry meaning and must survive.
        ("vitamin d", "vitamin"),
        ("e coli", "coli genome"),
        ("k means", "means"),
        # Digits are content.
        ("GPT 4", "GPT 3"),
        ("COVID 19", "COVID"),
    ])
    def test_stays_distinct(self, a, b):
        assert canonical_key(a) != canonical_key(b)


class TestEdgeCases:
    def test_empty_and_none(self):
        assert canonical_key("") == ""
        assert canonical_key(None) == ""
        assert canonical_key("   ") == ""

    def test_punctuation_only(self):
        assert canonical_key("!!!") == ""

    def test_all_grammar_words_keep_a_key(self):
        """An all-stopword query still needs to be distinguishable."""
        assert canonical_key("what is the") != ""
        assert canonical_key("what is the") != canonical_key("how do you")

    def test_key_is_order_independent_and_deduped(self):
        assert canonical("learning machine learning").tokens == ("learning", "machine")

    def test_display_preserves_case_and_wording(self):
        c = canonical("  Deep   Learning for NLP ")
        assert c.display == "Deep Learning for NLP"
        assert c.key != c.display

    def test_unicode_compatibility_forms_fold(self):
        # NFKC maps the ﬁ ligature to "fi" and full-width to ASCII.
        assert canonical_key("ﬁne tuning") == canonical_key("fine tuning")
        assert canonical_key("ＬＬＭ") == canonical_key("LLM")

    def test_acronyms_survive(self):
        for q in ("LLM", "NLP", "CNN", "TCP"):
            assert canonical_key(q), f"{q} must produce a key"

    def test_idempotent(self):
        k = canonical_key("Machine Learning in Healthcare")
        assert canonical_key(k) == k


class TestCacheKeyWiring:
    """The two caches that consume canonical identity agree with each other."""

    def test_relevance_cache_key_uses_canonical_topic(self):
        from ai.relevance import _cache_key

        paper = {"title": "Attention Is All You Need"}
        assert _cache_key("Machine Learning", paper) == _cache_key("machine  learning", paper)

    def test_search_cache_key_collapses_variants(self):
        from integrations.paper_search import SHARED_LIMIT_PER_SOURCE

        def key(q):
            return f"{canonical_key(q)}_{SHARED_LIMIT_PER_SOURCE}_True_all"

        assert key("Machine Learning") == key("machine learning")
        assert key("Machine Learning") != key("deep learning")
