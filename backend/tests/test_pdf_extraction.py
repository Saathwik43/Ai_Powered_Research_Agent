import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from ai.evidence_extraction import _evidence_cache, extract_evidence_for_paper
from ai.pdf_extraction import _grobid_state, extract_evidence_from_pdf_with_source


MINIMAL_SECTIONED_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 459 >>
stream
BT
/F1 12 Tf
72 740 Td
(Abstract) Tj
0 -18 Td
(This study evaluates retrieval augmented literature survey generation.) Tj
0 -24 Td
(Methods) Tj
0 -18 Td
(We fine tune a transformer and evaluate it on a benchmark corpus.) Tj
0 -24 Td
(Dataset) Tj
0 -18 Td
(Experiments use the SciBench corpus with 1200 labeled papers.) Tj
0 -24 Td
(Results) Tj
0 -18 Td
(The approach improves F1 score by 8 percent over the baseline.) Tj
0 -24 Td
(Limitations) Tj
0 -18 Td
(Coverage is limited to English language publications.) Tj
0 -24 Td
(Future Work) Tj
0 -18 Td
(Future work will extend the system to multilingual datasets.) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000010 00000 n 
0000000063 00000 n 
0000000122 00000 n 
0000000248 00000 n 
0000000318 00000 n 
trailer
<< /Root 1 0 R /Size 6 >>
startxref
828
%%EOF
"""


@pytest.fixture(autouse=True)
def clear_state():
    _evidence_cache.clear()
    _grobid_state["available"] = None
    _grobid_state["checked_at"] = 0.0
    yield
    _evidence_cache.clear()
    _grobid_state["available"] = None
    _grobid_state["checked_at"] = 0.0


@pytest.mark.integration
@pytest.mark.anyio
async def test_real_docling_pdf_extraction_skips_grobid_and_llm():
    pytest.importorskip("docling")

    paper = {
        "title": "Docling Integration Paper",
        "abstract": "LLM should not be used here.",
        "oa_url": "https://example.com/paper.pdf",
    }

    with patch("ai.pdf_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=MINIMAL_SECTIONED_PDF)), \
         patch("ai.pdf_extraction._extract_with_grobid", new=AsyncMock()) as mock_grobid, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock()) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "docling"
    assert "retrieval augmented literature survey generation" in evidence["objective"].lower()
    assert "benchmark corpus" in evidence["method"].lower()
    assert "scibench corpus" in evidence["dataset"].lower()
    assert "f1 score" in evidence["results"].lower()
    mock_grobid.assert_not_called()
    mock_llm.assert_not_called()


@pytest.mark.anyio
async def test_docling_low_confidence_falls_back_to_grobid():
    grobid_evidence = {
        "objective": "Study objective from GROBID.",
        "method": "Method from GROBID.",
        "dataset": "",
        "results": "Results from GROBID.",
        "limitations": "",
        "future_work": "",
    }

    with patch("ai.pdf_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.pdf_extraction._extract_with_docling", return_value={"objective": "Only one field"}), \
         patch("ai.pdf_extraction._extract_with_grobid", new=AsyncMock(return_value=grobid_evidence)) as mock_grobid:
        evidence, source = await extract_evidence_from_pdf_with_source("https://example.com/paper.pdf")

    assert source == "grobid"
    assert evidence == grobid_evidence
    mock_grobid.assert_awaited_once()


@pytest.mark.anyio
async def test_grobid_down_fast_falls_back_to_llm_without_retry_storm():
    llm_json = json.dumps(
        {
            "objective": "Objective from abstract.",
            "method": "Method from abstract.",
            "dataset": "",
            "results": "Results from abstract.",
            "limitations": "",
            "future_work": "",
        }
    )
    _grobid_state["available"] = False
    _grobid_state["checked_at"] = time.time()

    paper_one = {"title": "Paper One", "abstract": "Abstract one", "oa_url": "https://example.com/one.pdf"}
    paper_two = {"title": "Paper Two", "abstract": "Abstract two", "oa_url": "https://example.com/two.pdf"}

    with patch("ai.pdf_extraction._fetch_pdf_bytes", new=AsyncMock(return_value=b"%PDF")), \
         patch("ai.pdf_extraction._extract_with_docling", return_value=None), \
         patch("ai.pdf_extraction.httpx.AsyncClient.post", new=AsyncMock()) as mock_post, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock(return_value=llm_json)) as mock_llm:
        evidence_one, source_one = await extract_evidence_for_paper(paper_one)
        evidence_two, source_two = await extract_evidence_for_paper(paper_two)

    assert source_one == "llm-fallback"
    assert source_two == "llm-fallback"
    assert evidence_one["objective"] == "Objective from abstract."
    assert evidence_two["results"] == "Results from abstract."
    mock_post.assert_not_called()
    assert mock_llm.await_count == 2


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

    with patch("ai.pdf_extraction._fetch_pdf_bytes", new=AsyncMock()) as mock_fetch, \
         patch("ai.pdf_extraction._extract_with_grobid", new=AsyncMock()) as mock_grobid, \
         patch("ai.evidence_extraction.generate_completion", new=AsyncMock(return_value=llm_json)) as mock_llm:
        evidence, source = await extract_evidence_for_paper(paper)

    assert source == "llm-fallback"
    assert evidence["objective"] == "Objective from LLM."
    mock_fetch.assert_not_called()
    mock_grobid.assert_not_called()
    mock_llm.assert_awaited_once()
