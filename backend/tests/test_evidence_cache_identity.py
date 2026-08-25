"""B6 — extracted evidence must not be shared between different papers.

The cache key was the first 60 characters of the normalised title. Two
consequences:

* two papers whose titles agree for 60 characters (a "Part I" / "Part II" pair)
  shared one evidence record, and
* an *untitled* paper produced the empty string, so every such record collided
  on a single entry — and evidence extracted from one user's upload came back
  as the answer for the next user's.

It now keys on `core.paper_identity`, the same definition search dedupe uses.
"""

from ai import evidence_extraction as ee


def setup_function():
    ee._evidence_cache.clear()


def _evidence(text):
    return {
        "objective": text, "method": "", "dataset": "",
        "results": "", "limitations": "", "future_work": "",
    }


def test_two_untitled_papers_do_not_share_a_cache_entry():
    mine = {"title": "", "abstract": "my private uploaded results: 94.2% on our internal set"}
    theirs = {"title": "", "abstract": "an unrelated paper about crop yields"}

    ee._store_cached_evidence(mine, _evidence("mine"), "llm-fallback")

    assert ee._get_cached_evidence(theirs) is None
    assert ee._get_cached_evidence(mine)[0]["objective"] == "mine"


def test_a_paper_with_nothing_to_identify_it_is_not_cached_at_all():
    """No title, no DOI, no abstract: any key would be shared with the next
    equally anonymous paper, so nothing is stored."""
    blank = {"title": "", "abstract": ""}
    assert ee._cache_key(blank) is None

    ee._store_cached_evidence(blank, _evidence("leak"), "llm-fallback")
    assert ee._evidence_cache == {}
    assert ee._get_cached_evidence({"title": "", "abstract": ""}) is None


def test_titles_agreeing_for_sixty_characters_are_kept_apart():
    long_prefix = "A Comprehensive Survey of Neural Architectures for Semantic Segmentation"
    part_one = {"title": f"{long_prefix}, Part I"}
    part_two = {"title": f"{long_prefix}, Part II"}
    assert len(long_prefix) > 60

    ee._store_cached_evidence(part_one, _evidence("part one"), "arxiv-html")
    assert ee._get_cached_evidence(part_two) is None


def test_doi_wins_over_title_so_the_same_paper_hits():
    from_openalex = {"title": "Attention Is All You Need", "doi": "10.5555/3295222"}
    from_crossref = {"title": "ATTENTION IS ALL YOU NEED!", "doi": "https://doi.org/10.5555/3295222"}

    ee._store_cached_evidence(from_openalex, _evidence("transformer"), "arxiv-html")
    hit = ee._get_cached_evidence(from_crossref)
    assert hit is not None
    assert hit[0]["objective"] == "transformer"


def test_same_title_still_hits():
    """The cache must still do its job: one paper arriving twice in a fan-out
    is classified once."""
    paper = {"title": "Deep Residual Learning for Image Recognition", "year": 2016}
    ee._store_cached_evidence(paper, _evidence("resnet"), "pdf-structure")
    assert ee._get_cached_evidence(dict(paper))[0]["objective"] == "resnet"


def test_key_is_never_the_empty_string():
    for paper in (
        {"title": "", "abstract": "something"},
        {"title": "   ", "abstract": "something else"},
        {"title": None, "abstract": "x" * 50},
    ):
        assert ee._cache_key(paper) not in ("", None) or ee._cache_key(paper) is None
        key = ee._cache_key(paper)
        if key is not None:
            assert key != ""
