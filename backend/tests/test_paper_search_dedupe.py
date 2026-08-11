import pytest
from integrations.paper_search import (
    _deduplicate,
    _identity_keys,
    _normalize_arxiv_id,
    _normalize_doi,
    _normalize_title,
)


def test_normalize_doi():
    assert _normalize_doi("10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("https://doi.org/10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("http://doi.org/10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("doi:10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("  10.1234/5678  ") == "10.1234/5678"
    assert _normalize_doi("https://arxiv.org/abs/1706.03762") == ""


class TestNormalizeTitle:
    def test_punctuation_folds_to_a_single_space(self):
        """Deleting punctuation instead of folding it produced two keys for
        one title, so both copies survived dedup."""
        assert _normalize_title("deep-learning") == "deep learning"
        assert _normalize_title("deep — learning") == "deep learning"
        assert _normalize_title("Deep   Learning") == "deep learning"
        assert _normalize_title("deep-learning") == _normalize_title("deep — learning")

    def test_handles_empty_and_none(self):
        assert _normalize_title("") == ""
        assert _normalize_title(None) == ""


class TestNormalizeArxivId:
    @pytest.mark.parametrize("paper", [
        {"url": "https://arxiv.org/abs/1706.03762"},
        {"url": "https://arxiv.org/abs/1706.03762v5"},
        {"pdf_url": "https://arxiv.org/pdf/1706.03762.pdf"},
        {"id": "arXiv:1706.03762"},
        {"doi": "10.48550/arXiv.1706.03762"},
    ])
    def test_all_spellings_yield_one_id(self, paper):
        assert _normalize_arxiv_id(paper) == "1706.03762"

    def test_old_style_identifier(self):
        assert _normalize_arxiv_id({"url": "https://arxiv.org/abs/math.GT/0309136"}) == "math.gt/0309136"

    def test_absent(self):
        assert _normalize_arxiv_id({"url": "https://doi.org/10.1234/x"}) == ""
        assert _normalize_arxiv_id({}) == ""


class TestDeduplicate:
    def test_by_doi(self):
        papers = [
            {"title": "A highly novel approach to machine learning",
             "doi": "https://doi.org/10.1234/ml.2023.01"},
            {"title": "A highly novel approach to ML (truncated)",
             "doi": "10.1234/ml.2023.01"},
        ]
        unique = _deduplicate(papers)
        assert len(unique) == 1
        assert unique[0]["title"] == "A highly novel approach to machine learning"

    def test_fallback_to_title(self):
        papers = [
            {"title": "Attention is all you need", "doi": ""},
            {"title": "Attention is all you need", "url": "https://arxiv.org/abs/1706.03762"},
        ]
        assert len(_deduplicate(papers)) == 1

        papers_no_doi = [
            {"title": "Some exact title here"},
            {"title": "Some EXACT Title here."},
        ]
        assert len(_deduplicate(papers_no_doi)) == 1

    def test_preprint_and_published_version_merge(self):
        """The preprint carries the arXiv id, the published record the DOI.
        They share a title, so they must collapse to one enriched record."""
        papers = [
            {"title": "Attention Is All You Need", "source": "arXiv",
             "url": "https://arxiv.org/abs/1706.03762",
             "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
             "citations": 0, "year": "2017"},
            {"title": "Attention is all you need.", "source": "Semantic Scholar",
             "doi": "10.5555/3295222.3295349", "citations": 90000, "year": "2017"},
        ]
        unique = _deduplicate(papers)
        assert len(unique) == 1
        merged = unique[0]
        # Higher-cited record wins conflicts...
        assert merged["citations"] == 90000
        assert merged["doi"] == "10.5555/3295222.3295349"
        # ...but the preprint still contributes what it alone had.
        assert merged["pdf_url"] == "https://arxiv.org/pdf/1706.03762.pdf"

    def test_merge_fills_placeholder_fields(self):
        papers = [
            {"title": "Graph Neural Networks", "doi": "10.1/a",
             "abstract": "No abstract available", "authors": "Unknown Authors", "citations": 5},
            {"title": "Graph Neural Networks", "doi": "10.1/a",
             "abstract": "A real abstract about GNNs.", "authors": "Kipf, Welling", "citations": 3},
        ]
        merged = _deduplicate(papers)[0]
        assert merged["abstract"] == "A real abstract about GNNs."
        assert merged["authors"] == "Kipf, Welling"
        assert merged["citations"] == 5

    def test_long_titles_are_not_truncated(self):
        """A 60-char prefix key collapsed genuinely different papers."""
        papers = [
            {"title": "A Survey of Deep Learning Methods for Medical Image Segmentation Part I",
             "doi": "10.1/part1"},
            {"title": "A Survey of Deep Learning Methods for Medical Image Segmentation Part II",
             "doi": "10.1/part2"},
        ]
        assert len(_deduplicate(papers)) == 2

    def test_short_generic_titles_are_qualified_by_year(self):
        papers = [
            {"title": "Editorial", "year": "2022"},
            {"title": "Editorial", "year": "2023"},
            {"title": "Editorial", "year": "2023"},
        ]
        assert len(_deduplicate(papers)) == 2

    def test_untitled_papers_do_not_poison_each_other(self):
        """One empty title used to claim the '' key and discard every later
        untitled paper, DOI or not."""
        papers = [
            {"title": "", "doi": "10.1/a"},
            {"title": "", "doi": "10.1/b"},
            {"title": "", "doi": "10.1/c"},
        ]
        assert len(_deduplicate(papers)) == 3

    def test_record_with_no_identifier_at_all_is_dropped(self):
        papers = [
            {"title": "", "abstract": "orphan"},
            {"title": "Real Paper Title", "doi": "10.1/a"},
        ]
        unique = _deduplicate(papers)
        assert len(unique) == 1
        assert unique[0]["title"] == "Real Paper Title"

    def test_distinct_papers_are_kept(self):
        papers = [
            {"title": "Paper One", "doi": "10.1/one", "year": "2020"},
            {"title": "Paper Two", "doi": "10.1/two", "year": "2021"},
            {"title": "Paper Three", "url": "https://arxiv.org/abs/2101.00001"},
        ]
        assert len(_deduplicate(papers)) == 3

    def test_three_way_merge_via_transitive_identifiers(self):
        """A shares a title with B, B shares a DOI with C — all one paper."""
        papers = [
            {"title": "Transitive Merge Example", "url": "https://arxiv.org/abs/2101.00002"},
            {"title": "Transitive Merge Example", "doi": "10.9/xyz", "citations": 10},
            {"title": "Completely Different Wording", "doi": "10.9/xyz", "citations": 12},
        ]
        unique = _deduplicate(papers)
        assert len(unique) == 1
        assert unique[0]["citations"] == 12

    def test_result_order_follows_input_order(self):
        papers = [
            {"title": "First Paper Here", "doi": "10.1/1"},
            {"title": "Second Paper Here", "doi": "10.1/2"},
            {"title": "First Paper Here", "doi": "10.1/1"},
        ]
        unique = _deduplicate(papers)
        assert [p["title"] for p in unique] == ["First Paper Here", "Second Paper Here"]

    def test_ranking_fields_survive_a_merge(self):
        papers = [
            {"title": "Ranked Paper Title", "doi": "10.1/r", "_relevance_rank": 0.87, "citations": 1},
            {"title": "Ranked Paper Title", "doi": "10.1/r", "citations": 99},
        ]
        merged = _deduplicate(papers)[0]
        assert merged["_relevance_rank"] == 0.87


class TestIdentityKeys:
    def test_all_three_identifier_kinds_are_emitted(self):
        keys = _identity_keys({
            "title": "Attention Is All You Need",
            "doi": "10.5555/x",
            "url": "https://arxiv.org/abs/1706.03762",
        })
        assert "doi:10.5555/x" in keys
        assert "arxiv:1706.03762" in keys
        assert "title:attention is all you need" in keys

    def test_unusable_record_has_no_keys(self):
        assert _identity_keys({"abstract": "orphan"}) == []
