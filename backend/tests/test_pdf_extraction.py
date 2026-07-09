import json
from unittest.mock import AsyncMock, patch

import pytest

from ai.evidence_extraction import _evidence_cache, extract_evidence_for_paper

@pytest.fixture(autouse=True)
def clear_state():
    _evidence_cache.clear()
    yield
    _evidence_cache.clear()


@pytest.mark.anyio
async def test_grobid_client_extraction_skips_llm():
    paper = {
        "title": "Grobid Client Paper",
        "abstract": "LLM should not be used here.",
        "oa_url": "https://example.com/paper.pdf",
    }
    
    grobid_mock_result = {
        "title": "Grobid Client Paper",
        "authors": ["Author One"],
        "abstract": "This study evaluates retrieval augmented literature survey generation.",
        "sections": {
            "methods": "We fine tune a transformer and evaluate it on a benchmark corpus.",
            "dataset": "Experiments use the SciBench corpus with 1200 labeled papers.",
            "results": "The approach improves F1 score by 8 percent over the baseline.",
            "limitations": "Coverage is limited to English language publications.",
            "future work": "Future work will extend the system to multilingual datasets."
        },
        "confidence": {"title": "high", "authors": "high", "abstract": "high", "sections": "high"}
    }

    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.evidence_extraction.extract_via_grobid", new=AsyncMock(return_value=grobid_mock_result)) as mock_grobid, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "grobid"
    assert "retrieval augmented literature survey generation" in evidence["objective"].lower()
    assert "benchmark corpus" in evidence["method"].lower()
    assert "scibench corpus" in evidence["dataset"].lower()
    assert "f1 score" in evidence["results"].lower()
    mock_grobid.assert_awaited_once_with(b"%PDF")
    mock_llm.assert_not_called()


@pytest.mark.anyio
async def test_grobid_returns_no_usable_evidence_falls_back_to_llm():
    llm_json = json.dumps(
        {
            "objective": "Objective from LLM.",
            "method": "Method from LLM.",
            "dataset": "",
            "results": "Results from LLM.",
            "limitations": "",
            "future_work": "",
        }
    )
    paper = {"title": "Paper", "abstract": "Abstract", "oa_url": "https://example.com/paper.pdf"}

    # Grobid returns empty dictionary (no usable evidence)
    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.evidence_extraction.extract_via_grobid", new=AsyncMock(return_value={})) as mock_grobid, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock(return_value=llm_json)) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "llm-fallback"
    assert evidence["objective"] == "Objective from LLM."
    mock_grobid.assert_awaited_once_with(b"%PDF")
    mock_llm.assert_awaited_once()


@pytest.mark.anyio
async def test_no_oa_url_goes_directly_to_llm():
    llm_json = json.dumps(
        {
            "objective": "Objective from LLM.",
            "method": "Method from LLM.",
            "dataset": "",
            "results": "",
            "limitations": "",
            "future_work": "",
        }
    )
    paper = {"title": "No OA URL Paper", "abstract": "Abstract only"}

    with patch("ai.evidence_extraction._fetch_pdf_bytes", new=AsyncMock()) as mock_fetch, \
         patch("ai.evidence_extraction.extract_via_grobid", new=AsyncMock()) as mock_grobid, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock(return_value=llm_json)) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "llm-fallback"
    assert evidence["objective"] == "Objective from LLM."
    mock_fetch.assert_not_called()
    mock_grobid.assert_not_called()
    mock_llm.assert_awaited_once()
