"""
Paper identity (audit A5) and the removal of the dead `diversify` flag (A12).

A5: the relevance-verdict cache keyed on the first 60 characters of the title,
so two different papers sharing that prefix shared one yes/no verdict. It now
keys on core.paper_identity, the same identity the dedupe index uses.

A12: `diversify` was never passed as True by any caller, yet it widened the
search cache key, so two callers differing only in a no-op parameter each paid
a full 11-source fan-out.
"""

import inspect
import unittest
from unittest.mock import patch

from ai.relevance import _cache_key
from core.paper_identity import (
    identity_keys,
    normalize_arxiv_id,
    normalize_title,
    paper_doi,
    paper_identity,
)
import integrations.paper_search as ps


# ── A5 — identity ────────────────────────────────────────────────────────────

LONG_PREFIX = "Deep Learning Approaches for Multi Modal Medical Image Segmentation"


class TestPaperIdentity:
    def test_shared_60_char_prefix_is_not_shared_identity(self):
        """The exact A5 failure: same 60-char prefix, different papers."""
        part_one = {"title": f"{LONG_PREFIX} Part I"}
        part_two = {"title": f"{LONG_PREFIX} Part II"}
        assert part_one["title"][:60] == part_two["title"][:60]
        assert paper_identity(part_one) != paper_identity(part_two)

    def test_doi_outranks_title(self):
        by_doi = {"title": "Attention Is All You Need", "doi": "10.5555/3295222"}
        assert paper_identity(by_doi) == "doi:10.5555/3295222"

    def test_doi_url_forms_agree(self):
        assert paper_identity({"doi": "https://doi.org/10.1/ABC"}) == paper_identity(
            {"doi": "10.1/abc"}
        )

    def test_arxiv_used_when_no_doi(self):
        preprint = {"title": "A Preprint", "url": "https://arxiv.org/abs/2301.00001v2"}
        assert paper_identity(preprint) == "arxiv:2301.00001"

    def test_punctuation_variants_share_title_identity(self):
        assert paper_identity({"title": "Deep-Learning for Vision Systems"}) == paper_identity(
            {"title": "Deep — Learning for Vision Systems"}
        )

    def test_short_title_qualified_by_year(self):
        assert paper_identity({"title": "Editorial", "year": 2020}) != paper_identity(
            {"title": "Editorial", "year": 2024}
        )

    def test_unidentifiable_papers_do_not_collide(self):
        """No DOI, no arXiv id, no title — a content digest, not one shared key."""
        a = paper_identity({"abstract": "One study of protein folding."})
        b = paper_identity({"abstract": "An unrelated study of soil carbon."})
        assert a.startswith("blob:") and b.startswith("blob:")
        assert a != b

    def test_matches_dedupe_identity(self):
        """Relevance and dedupe must not drift apart — one definition, two users."""
        paper = {"title": "Attention Is All You Need", "doi": "10.5555/3295222"}
        assert paper_identity(paper) == identity_keys(paper)[0]
        assert ps._identity_keys(paper) == identity_keys(paper)

    def test_normalizers_are_the_shared_ones(self):
        assert ps._normalize_title is normalize_title
        assert ps._normalize_arxiv_id is normalize_arxiv_id
        assert ps._paper_doi is paper_doi


class TestRelevanceCacheKey:
    def test_prefix_collision_gets_separate_verdicts(self):
        part_one = {"title": f"{LONG_PREFIX} Part I"}
        part_two = {"title": f"{LONG_PREFIX} Part II"}
        assert _cache_key("segmentation", part_one) != _cache_key("segmentation", part_two)

    def test_same_paper_from_two_sources_shares_one_verdict(self):
        """Identical DOI, differently formatted title — one classification call."""
        crossref = {"title": "Attention Is All You Need", "doi": "10.5555/3295222"}
        openalex = {"title": "Attention is all you need!", "doi": "https://doi.org/10.5555/3295222"}
        assert _cache_key("transformers", crossref) == _cache_key("transformers", openalex)

    def test_topic_still_canonicalised(self):
        paper = {"title": "Attention Is All You Need"}
        assert _cache_key("Machine Learning", paper) == _cache_key("machine  learning", paper)

    def test_different_topics_do_not_share_a_verdict(self):
        paper = {"title": "Attention Is All You Need"}
        assert _cache_key("transformers", paper) != _cache_key("soil carbon", paper)


class TestFilteringIsPerPaper(unittest.IsolatedAsyncioTestCase):
    """
    Two papers sharing a 60-char title prefix each get classified.

    Under A5 the second paper hit the first one's cached verdict and was
    dropped or kept without ever being looked at.
    """

    def setUp(self):
        from ai import relevance

        relevance._relevance_cache.clear()
        relevance._failure_cache.clear()

    async def test_prefix_twins_are_both_classified(self):
        from ai import relevance

        seen = []

        async def fake_classify(topic, papers):
            seen.extend(p.get("title") for p in papers)
            return [p.get("title", "").endswith("Part I") for p in papers]  # only Part I

        with patch.object(relevance, "_classify_batch", fake_classify):
            kept = await relevance._filter_relevant_papers(
                "segmentation",
                [
                    {"title": f"{LONG_PREFIX} Part I", "abstract": "a"},
                    {"title": f"{LONG_PREFIX} Part II", "abstract": "b"},
                ],
            )

        assert len(seen) == 2, "both papers must be classified, not one via the other's cache"
        assert [p["title"] for p in kept] == [f"{LONG_PREFIX} Part I"]

    async def test_second_copy_of_one_paper_reuses_the_verdict(self):
        """Cache still works where it should: same DOI, second call is free."""
        from ai import relevance

        calls = []

        async def fake_classify(topic, papers):
            calls.append(len(papers))
            return [False] * len(papers)

        with patch.object(relevance, "_classify_batch", fake_classify):
            paper = {"title": "A Study of Soil Carbon Flux", "doi": "10.1/abc", "abstract": "x"}
            await relevance._filter_relevant_papers("transformers", [paper])
            same = {"title": "A study of soil-carbon flux!", "doi": "10.1/ABC", "abstract": "y"}
            kept = await relevance._filter_relevant_papers("Transformers", [same])

        assert calls == [1], "the second request must be served from the verdict cache"
        assert kept == []


# ── A12 — diversify removed ──────────────────────────────────────────────────

class TestDiversifyRemoved:
    def test_no_diversify_parameter(self):
        for fn in (ps.search_all, ps.search_all_with_meta, ps._resolve_search, ps._execute_search):
            assert "diversify" not in inspect.signature(fn).parameters, fn.__name__

    def test_quota_helper_gone(self):
        assert not hasattr(ps, "_apply_diversity_quota")

    def test_cache_key_no_longer_carries_the_flag(self):
        from core.query_key import canonical_key

        key = f"{canonical_key('machine learning')}_{ps.SHARED_LIMIT_PER_SOURCE}_True_all"
        assert "False" not in key
