"""The per-request context report the PDF Analysis panel renders.

The panel's whole value is that it is *true*: if it says the paper fits when
the 40k cap cut it, it is worse than no panel. These tests pin the report to
the prompt that was actually assembled.
"""

import pytest

from ai import pdf_analysis


def _recording_completion(captured):
    async def _complete(system_prompt=None, user_prompt=None, **kwargs):
        captured.append({
            "system_prompt": system_prompt or "",
            "user_prompt": user_prompt or "",
            "max_tokens": kwargs.get("max_tokens"),
            "cached_content": kwargs.get("cached_content"),
        })
        return '{"gaps": [], "well_covered": [], "suggested_direction": ""}'
    return _complete


PAPER = (
    "This paper studies intrusion detection on network traffic. "
    "We evaluate three public datasets and report a 12% improvement. "
) * 40


@pytest.mark.anyio
async def test_a_question_reports_every_part_of_its_prompt(monkeypatch):
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))

    result = await pdf_analysis.analyze_uploaded_paper(
        PAPER, custom_prompt="Which datasets were used?",
    )

    context = result["context"]
    assert context["question_chars"] == len("Which datasets were used?")
    assert context["max_output_tokens"] == pdf_analysis._CUSTOM_MAX_OUTPUT
    assert context["paper_char_cap"] == pdf_analysis._CUSTOM_CONTEXT_CHARS
    assert context["chars_per_token"] == pdf_analysis._CHARS_PER_TOKEN
    assert context["cached"] is False
    # No conversation on a first turn — the panel drops that segment.
    assert context["conversation_chars"] == 0


@pytest.mark.anyio
async def test_the_reported_sizes_match_the_prompt_that_was_sent(monkeypatch):
    """Not a restatement of the inputs: these are read back off the call."""
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))

    result = await pdf_analysis.analyze_uploaded_paper(
        PAPER, custom_prompt="Summarise the method.",
    )

    context = result["context"]
    call = sent[-1]
    assert context["instructions_chars"] == len(call["system_prompt"])
    assert context["max_output_tokens"] == call["max_tokens"]
    # The paper is embedded in the user prompt, so the prompt can only be
    # larger — but never smaller than what we claim we sent.
    assert len(call["user_prompt"]) >= context["paper_chars"]


@pytest.mark.anyio
async def test_a_paper_over_the_cap_is_reported_as_truncated(monkeypatch):
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))
    huge = "Intrusion detection on network traffic. " * 3000  # ~117k chars

    result = await pdf_analysis.analyze_uploaded_paper(
        huge, custom_prompt="What is the contribution?",
    )

    context = result["context"]
    assert context["paper_truncated"] is True
    assert context["paper_chars"] <= pdf_analysis._CUSTOM_CONTEXT_CHARS


@pytest.mark.anyio
async def test_a_paper_under_the_cap_is_not_reported_as_truncated(monkeypatch):
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))

    result = await pdf_analysis.analyze_uploaded_paper(
        PAPER, custom_prompt="What is the contribution?",
    )

    assert result["context"]["paper_truncated"] is False


@pytest.mark.anyio
async def test_the_references_branch_still_reports_truncation(monkeypatch):
    """Asking about references takes a different cut — head plus tail — and
    that is still a cut. It used to be a separate `> 40000` check, which is
    exactly the kind of duplicate that drifts."""
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))
    huge = "Intrusion detection on network traffic. " * 3000

    result = await pdf_analysis.analyze_uploaded_paper(
        huge, custom_prompt="List the references.",
    )

    assert result["context"]["paper_truncated"] is True


@pytest.mark.anyio
async def test_gap_analysis_reports_its_own_smaller_budget(monkeypatch):
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))

    async def _no_evidence(paper):
        return {"objective": "", "method": "", "dataset": "",
                "results": "", "limitations": "", "future_work": ""}

    monkeypatch.setattr(pdf_analysis, "extract_evidence", _no_evidence)

    result = await pdf_analysis.analyze_uploaded_paper(PAPER)

    context = result["context"]
    assert result["type"] == "structured"
    assert context["max_output_tokens"] == pdf_analysis._GAP_MAX_OUTPUT
    assert context["paper_char_cap"] == pdf_analysis._GAP_CONTEXT_CHARS
    # No user question on this branch, and no conversation.
    assert context["question_chars"] == 0
    assert context["conversation_chars"] == 0


@pytest.mark.anyio
async def test_an_evidence_backed_gap_analysis_is_not_truncation(monkeypatch):
    """The evidence JSON is a summary by design. Flagging it as a cut would
    warn the reader about something that did not happen."""
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))

    async def _evidence(paper):
        return {"objective": "Detect intrusions.", "method": "A transformer.",
                "dataset": "Three public sets.", "results": "12% better.",
                "limitations": "", "future_work": ""}

    monkeypatch.setattr(pdf_analysis, "extract_evidence", _evidence)
    huge = "Intrusion detection on network traffic. " * 3000

    result = await pdf_analysis.analyze_uploaded_paper(huge)

    assert result["context"]["paper_truncated"] is False


@pytest.mark.anyio
async def test_a_metadata_answer_carries_no_context(monkeypatch):
    """Answered from the structure without a model call, so there is no window
    to report. The panel hides rather than showing the previous turn's sizes."""
    sent = []
    monkeypatch.setattr(pdf_analysis, "generate_completion", _recording_completion(sent))

    result = await pdf_analysis.analyze_uploaded_paper(
        PAPER,
        custom_prompt="Who are the authors?",
        structure={"title": "A paper", "authors": ["Lee, S."], "abstract": "", "sections": {}},
    )

    assert "context" not in result
    assert sent == []
