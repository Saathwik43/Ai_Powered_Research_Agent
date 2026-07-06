import pytest
from integrations.paper_search import _deduplicate, _normalize_doi

def test_normalize_doi():
    assert _normalize_doi("10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("https://doi.org/10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("http://doi.org/10.1234/5678") == "10.1234/5678"
    assert _normalize_doi("  10.1234/5678  ") == "10.1234/5678"

def test_deduplicate_by_doi():
    papers = [
        {
            "title": "A highly novel approach to machine learning",
            "doi": "https://doi.org/10.1234/ml.2023.01"
        },
        {
            "title": "A highly novel approach to ML (truncated)",
            "doi": "10.1234/ml.2023.01"
        }
    ]
    
    unique = _deduplicate(papers)
    assert len(unique) == 1
    assert unique[0]["title"] == "A highly novel approach to machine learning"

def test_deduplicate_fallback_to_title():
    papers = [
        {
            "title": "Attention is all you need",
            "doi": ""
        },
        {
            "title": "Attention is all you need",
            "url": "https://arxiv.org/abs/1706.03762"
        }
    ]
    
    unique = _deduplicate(papers)
    assert len(unique) == 1
    
    # Test that without DOIs, title still works
    papers_no_doi = [
        {"title": "Some exact title here"},
        {"title": "Some EXACT Title here."}
    ]
    
    unique_no_doi = _deduplicate(papers_no_doi)
    assert len(unique_no_doi) == 1
