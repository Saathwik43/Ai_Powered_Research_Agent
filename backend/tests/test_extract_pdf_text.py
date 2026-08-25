"""extract_pdf_text tiers: the paid, off-premises LlamaParse tier must not run
when LaTeX already produced text (B3), and must not run at all without the
uploader's consent (1.16)."""

from unittest.mock import AsyncMock, patch

import fitz
import pytest

from ai.pdf_analysis import extract_pdf_text
from core import processing_consent


def _pdf_with_text(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.anyio
async def test_arxiv_latex_skips_llamaparse():
    pdf = _pdf_with_text("arXiv:2301.12345v1 Attention Is All You Need")
    latex = {
        "abstract": "We propose the Transformer.",
        "sections": {
            "introduction": "Sequence transduction models.",
            "method": "Self attention replaces recurrence.",
        },
    }

    with patch("ai.pdf_analysis.fetch_latex_source", new=AsyncMock(return_value=latex)), \
         patch("ai.pdf_analysis.LlamaParse") as mock_parse:
        text = await extract_pdf_text(pdf, allow_third_party=True)

    mock_parse.assert_not_called()
    assert "We propose the Transformer" in text
    assert "Self attention replaces recurrence" in text


@pytest.mark.anyio
async def test_without_consent_the_file_never_leaves_this_server():
    """Tiers 2 and 3 run in-process on the same bytes, so declining costs parse
    quality on awkward PDFs — not the feature."""
    pdf = _pdf_with_text("A perfectly ordinary text PDF with no arXiv id.")

    with patch("ai.pdf_analysis.fetch_latex_source", new=AsyncMock(return_value=None)), \
         patch("ai.pdf_analysis.LlamaParse") as mock_parse:
        text = await extract_pdf_text(pdf)

    mock_parse.assert_not_called()
    assert "perfectly ordinary text PDF" in text


@pytest.mark.anyio
async def test_consent_defaults_to_off_and_fails_closed():
    state = processing_consent.consent_state(None)
    assert state["granted"] is False and state["current"] is False

    # An answer given against an older disclosure is not an answer to this one.
    stale = processing_consent.consent_state({
        processing_consent.CONSENT_FIELD: {"granted": True, "version": "1970-01-01"}
    })
    assert stale["granted"] is True
    assert stale["current"] is False


@pytest.mark.anyio
async def test_an_unreadable_consent_record_parses_locally():
    with patch("core.processing_consent._is_configured", return_value=True), \
         patch("core.auth._load_user", new=AsyncMock(side_effect=RuntimeError("mongo down"))):
        assert await processing_consent.user_allows_third_party("someone") is False
