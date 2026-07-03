"""
tests/test_citation_format.py
-----------------------------
Unit tests for deterministic citation formatting.
"""

import pytest
from ai.citation_format import format_citation, format_all_citations

def test_ieee_format_full():
    paper = {
        "title": "A Novel Architecture for Neural Networks",
        "authors": "John Doe, Jane Smith, and Alan Turing",
        "journal": "IEEE Trans. Neural Netw.",
        "year": "2023",
        "doi": "10.1109/TNNLS.2023.1234567"
    }
    citation = format_citation(paper, style="ieee")
    assert citation == 'J. Doe, J. Smith, and A. Turing, "A Novel Architecture for Neural Networks," IEEE Trans. Neural Netw., 2023. doi: https://doi.org/10.1109/TNNLS.2023.1234567'

def test_apa_format_full():
    paper = {
        "title": "A Novel Architecture for Neural Networks",
        "authors": "John Doe, Jane Smith, and Alan Turing",
        "journal": "Journal of Machine Learning Research",
        "year": "2023",
        "doi": "10.1109/TNNLS.2023.1234567"
    }
    citation = format_citation(paper, style="apa")
    assert citation == 'Doe, J., Smith, J., & Turing, A. (2023). A Novel Architecture for Neural Networks. *Journal of Machine Learning Research*. https://doi.org/10.1109/TNNLS.2023.1234567'

def test_chicago_format_full():
    paper = {
        "title": "A Novel Architecture for Neural Networks",
        "authors": "John Doe, Jane Smith, and Alan Turing",
        "journal": "Journal of Machine Learning Research",
        "year": "2023",
        "doi": "10.1109/TNNLS.2023.1234567"
    }
    citation = format_citation(paper, style="chicago")
    assert citation == 'Doe, John, Jane Smith, and Alan Turing. "A Novel Architecture for Neural Networks." *Journal of Machine Learning Research* (2023). https://doi.org/10.1109/TNNLS.2023.1234567.'

def test_oxford_alias():
    paper = {
        "title": "Test Title",
        "authors": "John Doe",
        "year": "2023"
    }
    chicago_cite = format_citation(paper, style="chicago")
    oxford_cite = format_citation(paper, style="oxford")
    assert chicago_cite == oxford_cite

def test_missing_fields_graceful_degradation():
    paper = {
        "title": "Just a Title",
        "authors": "Single Author"
    }
    # Should not crash, just omits year/journal/doi
    ieee = format_citation(paper, style="ieee")
    assert ieee == 'S. Author, "Just a Title,".'
    
    apa = format_citation(paper, style="apa")
    assert apa == 'Author, S. (n.d.). Just a Title.'
    
    chicago = format_citation(paper, style="chicago")
    assert chicago == 'Author, Single. "Just a Title."'

def test_chicago_no_double_period_et_al():
    paper = {
        "title": "A Review of Generative Models",
        "authors": "Alice Adams, Bob Brown, Charlie Clark, Dave Davis",
        "journal": "AI Today",
        "year": "2024"
    }
    citation = format_citation(paper, style="chicago")
    # First author is inverted, >3 authors triggers "et al."
    # Should be 'Adams, Alice, et al. "A Review..."' without double period.
    assert "et al. " in citation
    assert "et al.. " not in citation
    assert citation == 'Adams, Alice, et al. "A Review of Generative Models." *AI Today* (2024).'
