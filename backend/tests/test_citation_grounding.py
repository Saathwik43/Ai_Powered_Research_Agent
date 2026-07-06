"""
tests/test_citation_grounding.py
---------------------------------
Unit tests for ai.citation_grounding (Phase 3).

No pytest-asyncio dependency is added — matching the rest of this suite,
async functions are driven with asyncio.run() from plain sync test functions.
"""

import asyncio
from unittest.mock import patch, AsyncMock

from ai.citation_grounding import check_citation_grounding, _split_sentences, _extract_markers

REFERENCES = {
    "1": {
        "title": "RAG Reduces Hallucination",
        "evidence": {
            "objective": "Study RAG's effect on factual accuracy.",
            "results": "Reduced hallucination rate by 40% relative to baseline generation.",
        },
    },
    "2": {
        "title": "Dense Retrieval Gains",
        "evidence": {
            "results": "12% improvement in factual accuracy on the NQ dataset when "
                       "combining dense retrieval with generation.",
        },
    },
}


def _mock_verdict(*args, **kwargs):
    return '''
    {
      "results": [
        {"sentence_id": 0, "status": "grounded", "note": ""},
        {"sentence_id": 1, "status": "partial", "note": "Evidence is dataset-specific; sentence generalizes."}
      ]
    }
    '''


class TestSentenceSplitting:

    def test_splits_on_sentence_boundaries(self):
        text = "First claim [1]. Second claim [2]."
        sents = _split_sentences(text)
        assert sents == ["First claim [1].", "Second claim [2]."]

    def test_protects_abbreviations_and_decimals(self):
        text = "Lee et al. reported a 3.5k-question benchmark [4]. It remains unevaluated."
        sents = _split_sentences(text)
        assert len(sents) == 2
        assert "et al." in sents[0]
        assert "3.5k" in sents[0]

    def test_extract_markers_handles_grouped_citations(self):
        assert _extract_markers("Supported by prior work [1,3].") == ["1", "3"]
        assert _extract_markers("No citation here.") == []


class TestCheckCitationGrounding:

    def test_no_references_returns_empty(self):
        result = asyncio.run(check_citation_grounding("Some text [1].", {}))
        assert result == {}

    def test_no_cited_sentences_flags_uncited_claims_only(self):
        content = "Our approach achieves state-of-the-art results on this benchmark."
        result = asyncio.run(check_citation_grounding(content, REFERENCES))
        assert "citation_map" not in result
        assert len(result["uncited_claims"]) == 1

    @patch("ai.citation_grounding.generate_completion", new_callable=AsyncMock)
    def test_grounded_and_partial_classification(self, mock_gen):
        mock_gen.side_effect = _mock_verdict
        content = (
            "RAG reduces hallucination rates by 40% relative to baseline generation [1]. "
            "Dense retrieval always improves factual accuracy across all datasets [2]."
        )
        result = asyncio.run(check_citation_grounding(content, REFERENCES))

        assert len(result["citation_map"]) == 2
        assert result["citation_map"][0]["status"] == "grounded"
        assert result["citation_map"][0]["note"] == ""
        assert result["citation_map"][1]["status"] == "partial"
        assert "generalizes" in result["citation_map"][1]["note"]

    @patch("ai.citation_grounding.generate_completion", new_callable=AsyncMock)
    def test_only_cited_papers_evidence_is_sent(self, mock_gen):
        """Evidence for uncited papers must not leak into the prompt — keeps the
        model from matching a claim to a paper that wasn't actually cited for it."""
        mock_gen.side_effect = _mock_verdict
        content = "RAG reduces hallucination rates by 40% relative to baseline generation [1]."
        asyncio.run(check_citation_grounding(content, REFERENCES))

        sent_prompt = mock_gen.call_args.kwargs["user_prompt"]
        assert "[1]" in sent_prompt
        assert "Dense Retrieval Gains" not in sent_prompt  # paper [2] wasn't cited

    @patch("ai.citation_grounding.generate_completion", new_callable=AsyncMock)
    def test_llm_failure_fails_closed(self, mock_gen):
        mock_gen.side_effect = RuntimeError("all providers down")
        content = "RAG reduces hallucination rates by 40% relative to baseline generation [1]."
        result = asyncio.run(check_citation_grounding(content, REFERENCES))

        assert result["citation_map"][0]["status"] == "unverified"
        assert result["citation_map"][0]["note"] == "grounding check unavailable"

    def test_falls_back_to_abstract_when_no_evidence(self):
        refs = {"1": {"title": "Old-Style Paper", "abstract": "A plain abstract with no evidence JSON."}}
        # Just confirm this doesn't raise when building the evidence block —
        # exercised indirectly through the happy path with a failing LLM call
        # so no real network/API call is made.
        with patch("ai.citation_grounding.generate_completion", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = RuntimeError("stubbed")
            result = asyncio.run(check_citation_grounding("Some claim [1].", refs))
        assert result["citation_map"][0]["cites"] == ["1"]
