"""
Tests for how ai/pdf_analysis.py assembles the context it sends to the model.

The chat prompt prefers parsed structure (GROBID, else the PyMuPDF heuristic)
over the raw extracted text. What matters here is the fallback: when structure
extraction produces nothing usable, the raw text must be used instead.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from ai import pdf_analysis
from ai.pdf_analysis import _MIN_USEFUL_STRUCTURE_CHARS, _build_paper_context

RAW_TEXT = "The full extracted text of the paper. " * 40


class TestStructureFallback:
    """
    Regression: the fallback test used to be

        len(json.dumps({"abstract": ..., "sections": ...}, indent=2)) > 20

    which measured the JSON *envelope* rather than its payload. An entirely
    empty structure serialises to 38 characters, so the condition was always
    true and the fallback never fired. With both extraction tiers failing the
    model received `{"abstract": "", "sections": {}}` and nothing else, and
    answered "the document does not contain that information" to every
    question -- while the perfectly readable extracted text sat unused.
    """

    @pytest.mark.parametrize("structure, label", [
        ({"abstract": "", "sections": {}}, "both extraction tiers failed"),
        ({"abstract": "", "sections": {"full_text": ""}}, "GROBID no-sections sentinel"),
        ({}, "keys missing entirely"),
        ({"abstract": None, "sections": None}, "null values"),
        ({"abstract": "", "sections": {"references": "   "}}, "whitespace-only section"),
        ({"abstract": "", "sections": {"method": None}}, "null section body"),
        ({"abstract": "too short", "sections": {}}, "below the useful-content floor"),
    ])
    def test_falls_back_to_raw_text(self, structure, label):
        assert _build_paper_context(structure, RAW_TEXT) == RAW_TEXT, (
            f"empty structure ({label}) starved the model of the paper text"
        )

    def test_real_structure_is_preferred_over_raw_text(self):
        structure = {
            "abstract": "A" * 250,
            "sections": {"method": "M" * 300, "results": "R" * 300},
        }
        out = _build_paper_context(structure, RAW_TEXT)

        assert out != RAW_TEXT
        assert "## Abstract" in out
        assert "## Method" in out
        assert "## Results" in out

    def test_heuristic_full_text_section_is_usable_structure(self):
        """
        pdf_structure.extract_structure falls back to {"full_text": ...} when it
        finds no headings. That carries real content and must not be discarded.
        """
        structure = {"abstract": "", "sections": {"full_text": "B" * 400}}
        out = _build_paper_context(structure, RAW_TEXT)

        assert out != RAW_TEXT
        assert "## Full Text" in out
        assert "B" * 400 in out

    def test_empty_sections_are_dropped_but_populated_ones_kept(self):
        structure = {
            "abstract": "",
            "sections": {"introduction": "", "method": "M" * 300, "references": "  "},
        }
        out = _build_paper_context(structure, RAW_TEXT)

        assert "## Method" in out
        assert "## Introduction" not in out
        assert "## References" not in out

    def test_threshold_is_measured_on_content_not_markup(self):
        """The floor must count prose, not the '## Heading' scaffolding."""
        body = "x" * (_MIN_USEFUL_STRUCTURE_CHARS - 1)
        assert _build_paper_context({"abstract": body, "sections": {}}, RAW_TEXT) == RAW_TEXT

        body = "x" * (_MIN_USEFUL_STRUCTURE_CHARS + 50)
        assert _build_paper_context({"abstract": body, "sections": {}}, RAW_TEXT) != RAW_TEXT

    def test_section_bodies_are_not_json_escaped(self):
        """
        Rendering as plain sections rather than json.dumps keeps real newlines
        instead of literal \\n escapes, which is what the model reads best.
        """
        structure = {"abstract": "", "sections": {"method": "line one\nline two\n" + "M" * 300}}
        out = _build_paper_context(structure, RAW_TEXT)

        assert "line one\nline two" in out
        assert "\\n" not in out


# ─── Branch selection ──────────────────────────────────────────────────────────

_STRUCTURED_REPLY = json.dumps({
    "well_covered": ["X is well established across the corpus"],
    "gaps": ["Y remains under-explored"],
    "suggested_direction": (
        "Run a longitudinal ablation over the sliding-window loss on non-stationary "
        "telemetry, reporting calibration error against a fixed clinical baseline."
    ),
})

_STRUCTURE = {"title": "T", "authors": ["A. Author"], "abstract": "Z" * 300, "sections": {}}


class TestBranchSelection:
    """
    Regression: analyze_uploaded_paper picks its structured gap-analysis branch by
    the *absence* of custom_prompt, but the only caller always sent one -- so the
    branch was dead code, the Findings tab could never show gaps, and the
    extract_evidence() call feeding that branch ran (and was discarded) on every
    single chat message.
    """

    @staticmethod
    def _patched(reply):
        evidence = AsyncMock(return_value={
            "objective": "o", "method": "m", "dataset": "",
            "results": "r", "limitations": "", "future_work": "",
        })
        generate = AsyncMock(return_value=reply)
        return evidence, generate, patch.multiple(
            pdf_analysis,
            extract_evidence=evidence,
            generate_completion=generate,
            get_or_create_gemini_cache=AsyncMock(return_value=None),
        )

    @pytest.mark.anyio
    async def test_absent_prompt_runs_structured_analysis(self):
        evidence, _gen, ctx = self._patched(_STRUCTURED_REPLY)
        with ctx:
            result = await pdf_analysis.analyze_uploaded_paper(RAW_TEXT, None, _STRUCTURE)

        assert result["type"] == "structured"
        assert result["data"]["gaps"] == ["Y remains under-explored"]
        assert evidence.await_count == 1

    @pytest.mark.anyio
    async def test_whitespace_prompt_is_treated_as_absent(self):
        """A stray space must not silently route a gap request into free-text chat."""
        _ev, _gen, ctx = self._patched(_STRUCTURED_REPLY)
        with ctx:
            result = await pdf_analysis.analyze_uploaded_paper(RAW_TEXT, "   ", _STRUCTURE)

        assert result["type"] == "structured"

    @pytest.mark.anyio
    async def test_chat_messages_do_not_pay_for_evidence_extraction(self):
        """
        extract_evidence feeds only the structured branch. Running it before the
        branch was chosen cost a discarded Groq call on the critical path of
        every user message.
        """
        evidence, _gen, ctx = self._patched("A prose answer about the method.")
        with ctx:
            result = await pdf_analysis.analyze_uploaded_paper(
                RAW_TEXT, "What is the method?", _STRUCTURE
            )

        assert result["type"] == "custom"
        assert evidence.await_count == 0, (
            "evidence extraction ran for a free-text question that never uses it"
        )
