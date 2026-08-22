"""Evidence-extraction ladder.

The hosted GROBID tier is gone (every free instance returns 503/404 and the
service is too heavy to self-host on the deploy target), so the ordering is now
arXiv HTML -> arXiv LaTeX -> Europe PMC JATS -> local PDF parse -> LLM. Each
test pins one rung and asserts the cheaper rungs above it were not consulted.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.evidence_extraction import _evidence_cache, extract_evidence_for_paper


@pytest.fixture(autouse=True)
def clear_state():
    _evidence_cache.clear()
    yield
    _evidence_cache.clear()


STRUCTURED_RESULT = {
    "title": "Structured Paper",
    "authors": ["Author One"],
    "abstract": "This study evaluates retrieval augmented literature survey generation.",
    "sections": {
        "methods": "We fine tune a transformer and evaluate it on a benchmark corpus.",
        "dataset": "Experiments use the SciBench corpus with 1200 labeled papers.",
        "results": "The approach improves F1 score by 8 percent over the baseline.",
        "limitations": "Coverage is limited to English language publications.",
        "future work": "Future work will extend the system to multilingual datasets.",
    },
    "confidence": {"title": "high", "authors": "high", "abstract": "high", "sections": "high"},
}


def _assert_structured_evidence(evidence):
    assert "retrieval augmented literature survey generation" in evidence["objective"].lower()
    assert "benchmark corpus" in evidence["method"].lower()
    assert "scibench corpus" in evidence["dataset"].lower()
    assert "f1 score" in evidence["results"].lower()


LLM_JSON = json.dumps(
    {
        "objective": "Objective from LLM.",
        "method": "Method from LLM.",
        "dataset": "",
        "results": "Results from LLM.",
        "limitations": "",
        "future_work": "",
    }
)


@pytest.mark.anyio
async def test_pdf_structure_extraction_skips_llm():
    paper = {
        "title": "Structured Paper",
        "abstract": "LLM should not be used here.",
        "oa_url": "https://example.com/paper.pdf",
    }

    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.evidence_extraction.extract_structure",
               new=MagicMock(return_value=STRUCTURED_RESULT)) as mock_structure, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "pdf-structure"
    _assert_structured_evidence(evidence)
    mock_structure.assert_called_once_with(b"%PDF")
    mock_llm.assert_not_called()


@pytest.mark.anyio
async def test_pdf_structure_with_no_usable_evidence_falls_back_to_llm():
    paper = {"title": "Paper", "abstract": "Abstract", "oa_url": "https://example.com/paper.pdf"}

    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.evidence_extraction.extract_structure",
               new=MagicMock(return_value={})) as mock_structure, \
         patch("ai.evidence_extraction.generate_completion",
               new=AsyncMock(return_value=LLM_JSON)) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "llm-fallback"
    assert evidence["objective"] == "Objective from LLM."
    mock_structure.assert_called_once_with(b"%PDF")
    mock_llm.assert_awaited_once()


@pytest.mark.anyio
async def test_unreadable_pdf_does_not_abort_extraction():
    """extract_structure raises on encrypted or truncated PDFs. One bad
    download must degrade to the LLM tier, not propagate out of the batch."""
    paper = {"title": "Broken PDF", "abstract": "Abstract", "oa_url": "https://example.com/x.pdf"}

    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.evidence_extraction.extract_structure",
               new=MagicMock(side_effect=RuntimeError("cannot open broken document"))), \
         patch("ai.evidence_extraction.generate_completion",
               new=AsyncMock(return_value=LLM_JSON)) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "llm-fallback"
    mock_llm.assert_awaited_once()


@pytest.mark.anyio
async def test_no_oa_url_goes_directly_to_llm():
    paper = {"title": "No OA URL Paper", "abstract": "Abstract only"}

    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock()) as mock_fetch, \
         patch("ai.evidence_extraction.extract_structure", new=MagicMock()) as mock_structure, \
         patch("ai.evidence_extraction.generate_completion",
               new=AsyncMock(return_value=LLM_JSON)) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "llm-fallback"
    assert evidence["objective"] == "Objective from LLM."
    mock_fetch.assert_not_called()
    mock_structure.assert_not_called()
    mock_llm.assert_awaited_once()


@pytest.mark.anyio
async def test_arxiv_html_preferred_over_latex_and_pdf():
    """arXiv's LaTeXML rendering carries an explicit section tree, so it must be
    tried before the LaTeX tarball and before any PDF is downloaded."""
    paper = {"title": "arXiv Paper", "url": "https://arxiv.org/abs/1706.03762"}

    with patch("ai.evidence_extraction.fetch_arxiv_html",
               new=AsyncMock(return_value=STRUCTURED_RESULT)) as mock_html, \
         patch("ai.evidence_extraction.fetch_latex_source", new=AsyncMock()) as mock_latex, \
         patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock()) as mock_fetch, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "arxiv-html"
    _assert_structured_evidence(evidence)
    mock_html.assert_awaited_once_with("1706.03762")
    mock_latex.assert_not_called()
    mock_fetch.assert_not_called()
    mock_llm.assert_not_called()


@pytest.mark.anyio
async def test_arxiv_falls_through_html_to_latex():
    """Papers older than arXiv's HTML rollout have no rendering on either host;
    the LaTeX tarball is the next rung, not the PDF."""
    paper = {"title": "Old arXiv Paper", "url": "https://arxiv.org/abs/0704.0001"}

    with patch("ai.evidence_extraction.fetch_arxiv_html", new=AsyncMock(return_value=None)), \
         patch("ai.evidence_extraction.fetch_latex_source",
               new=AsyncMock(return_value=STRUCTURED_RESULT)) as mock_latex, \
         patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock()) as mock_fetch, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "arxiv-latex"
    mock_latex.assert_awaited_once_with("0704.0001")
    mock_fetch.assert_not_called()
    mock_llm.assert_not_called()


@pytest.mark.anyio
async def test_europepmc_fulltext_used_before_pdf():
    """Open-access PMC records expose JATS XML with a real section tree, which
    beats inferring structure from PDF layout."""
    paper = {
        "title": "Biomedical Paper",
        "pmcid": "PMC3258128",
        "oa_url": "https://example.com/paper.pdf",
    }

    with patch("ai.evidence_extraction.fetch_full_text",
               new=AsyncMock(return_value=STRUCTURED_RESULT)) as mock_pmc, \
         patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock()) as mock_fetch, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "europepmc-fulltext"
    _assert_structured_evidence(evidence)
    mock_pmc.assert_awaited_once_with("PMC3258128")
    mock_fetch.assert_not_called()
    mock_llm.assert_not_called()


@pytest.mark.anyio
async def test_paywalled_pmc_record_falls_through_to_pdf():
    """Europe PMC answers 404 for records that are not open access, which
    fetch_full_text reports as None -- extraction must continue, not stop."""
    paper = {
        "title": "Paywalled Paper",
        "pmcid": "PMC9999999",
        "oa_url": "https://example.com/paper.pdf",
    }

    with patch("ai.evidence_extraction.fetch_full_text", new=AsyncMock(return_value=None)), \
         patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.evidence_extraction.extract_structure",
               new=MagicMock(return_value=STRUCTURED_RESULT)), \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "pdf-structure"
    mock_llm.assert_not_called()
