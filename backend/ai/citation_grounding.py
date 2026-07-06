"""
ai/citation_grounding.py
-------------------------
Phase 3 — Citation grounding (evidence-centric upgrade).

Replaces the binary `unverified_citations` heuristic in
`ai.manuscript_generation._check_unverified_citations` with a
sentence-level claim -> evidence mapping.

Why this design, given the existing pipeline
-------------------------------------------
generate_section() already forces the writer LLM to cite using
bracketed markers tied to a numbered reference list built from
Phase 2's evidence JSON (see manuscript_generation.py's `ref_text` /
`references_mapping`, and the identical pattern in gap_analysis.py).
That means the "which paper does this claim belong to" problem is
already solved by construction — the [N] marker says so.

What's NOT solved is whether the claim attributed to paper N is
actually *supported* by paper N's evidence, or whether it's a
plausible-sounding overreach. That's what this module checks:

  1. Split generated content into sentences.
  2. For each sentence, extract which [N] marker(s) it cites.
  3. Send only the cited sentences + the Phase-2 evidence for the
     specific papers they cite (not the whole corpus) to an LLM, and
     ask it to classify each as grounded / partial / unsupported /
     miscited.
  4. Flag sentences that assert a specific, citable-sounding claim but
     carry NO [N] marker at all — a cheap regex check, no LLM call.

Narrowing the evidence sent to only the cited paper(s) per sentence
(rather than searching the whole evidence store) keeps the prompt
small, keeps cost close to a single call per generated section
(matching gap_analysis.py's one-call structured-JSON pattern), and
stops the model from being tempted to "find a home" for a claim among
papers that weren't actually cited for it.

Output shape feeds `flags["citation_map"]`, which is a superset of
what Phase 4 (cross-paper comparison) and Phase 6 (reviewer agent)
need — both can consume this directly instead of re-deriving
grounding themselves.
"""

import json
import logging
import re

from ai.llm_provider import generate_completion

logger = logging.getLogger(__name__)

__all__ = ["check_citation_grounding"]

# Must match the field list in ai/evidence_extraction.py's schema.
_EVIDENCE_FIELDS = ["objective", "method", "dataset", "results", "limitations", "future_work"]

_CITATION_MARKER_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

# Placeholders used only to protect abbreviations/decimals during sentence
# splitting; restored immediately after.
_ABBR_PLACEHOLDER = "\x00"
_DEC_PLACEHOLDER = "\x01"

# Heuristic used ONLY to flag *uncited* sentences that look like specific
# factual claims worth a citation. Pure regex, no LLM call — mirrors
# numerical_validator.py's keyword-based approach. This is a recall check
# ("did the writer forget a citation?"), not a verification, so it stays
# cheap and is allowed to over-flag slightly.
_CLAIM_SIGNAL_RE = re.compile(
    r"\b(shows?|demonstrates?|found|reports?|achiev\w+|outperform\w+|"
    r"indicat\w+|reveal\w+|propose\w+|introduce\w+|\d)\b",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    """
    Lightweight sentence splitter — avoids adding an NLTK/spaCy dependency
    for something regex handles adequately for markdown prose, while
    protecting common abbreviations and decimal numbers from being split.
    """
    text = re.sub(r"\s+", " ", text.strip())
    protected = re.sub(
        r"\b(e\.g|i\.e|et al|Fig|vs)\.",
        lambda m: m.group(0).replace(".", _ABBR_PLACEHOLDER),
        text,
    )
    protected = re.sub(
        r"(\d)\.(\d)",
        lambda m: m.group(1) + _DEC_PLACEHOLDER + m.group(2),
        protected,
    )
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", protected)
    return [
        p.replace(_ABBR_PLACEHOLDER, ".").replace(_DEC_PLACEHOLDER, ".").strip()
        for p in parts
        if p.strip()
    ]


def _extract_markers(sentence: str) -> list[str]:
    """Return the numbered-reference indices a sentence cites, e.g. ['1', '3']."""
    indices: list[str] = []
    for m in _CITATION_MARKER_RE.finditer(sentence):
        for n in m.group(1).split(","):
            n = n.strip()
            if n and n not in indices:
                indices.append(n)
    return indices


def _evidence_block(idx: str, paper: dict) -> str:
    """Render one cited paper's Phase-2 evidence (or abstract fallback) for the prompt."""
    ev = paper.get("evidence", {}) or {}
    lines = [f"[{idx}] {paper.get('title', 'Unknown Title')}"]
    if any(ev.get(k) for k in _EVIDENCE_FIELDS):
        for k in _EVIDENCE_FIELDS:
            if ev.get(k):
                lines.append(f"  {k}: {ev[k]}")
    else:
        abstract = (paper.get("abstract", "") or "")[:400]
        lines.append(f"  abstract: {abstract}" if abstract else "  (no evidence or abstract available)")
    return "\n".join(lines)


_SYSTEM_PROMPT = """You are a citation-grounding verifier for an academic writing pipeline.
You do not write or improve prose. You check whether each cited sentence is actually
supported by the specific evidence provided for the paper(s) it cites.

Be strict:
- "grounded": the sentence's claim matches what the cited paper's evidence says, at a
  comparable level of specificity.
- "partial": the sentence overstates, understates, or overgeneralizes beyond what the
  cited evidence supports (e.g. evidence says "improved on one dataset", sentence implies
  it always improves).
- "unsupported": none of the cited paper(s)' evidence fields support this specific claim.
- "miscited": the claim is plausible but matches a DIFFERENT paper's evidence better than
  the one(s) actually cited here.

Numbers, percentages, named methods, and named datasets must match the cited evidence
precisely to count as "grounded" — a topical match alone is not enough.

Output ONLY valid JSON, no markdown, no explanation:
{
  "results": [
    {"sentence_id": 0, "status": "grounded|partial|unsupported|miscited", "note": ""}
  ]
}
"note" is required and non-empty for every status except "grounded" (empty string there).
Include exactly one entry for every sentence_id you were given, in order."""


async def check_citation_grounding(content: str, references_mapping: dict) -> dict:
    """
    Phase 3 citation grounding check — call after generate_completion() produces
    a section, alongside validate_numerical_claims().

    Parameters
    ----------
    content : str
        Generated section text containing [N]-style citation markers.
    references_mapping : dict[str, dict]
        The same mapping generate_section() already builds: str(index) -> paper
        dict, where each paper dict carries an 'evidence' sub-dict per
        ai.evidence_extraction's schema (falls back to 'abstract' if empty).

    Returns
    -------
    dict
        {"citation_map": [...], "uncited_claims": [...]}  (uncited_claims omitted
        if none found). Empty dict if there is nothing to check — e.g. no
        reference list was built for this section (mirrors the guard already in
        _check_unverified_citations for context-less generation).
    """
    if not references_mapping or not content:
        return {}

    sentences = _split_sentences(content)
    all_with_markers = [(i, s, _extract_markers(s)) for i, s in enumerate(sentences)]
    cited = [(i, s, refs) for i, s, refs in all_with_markers if refs]

    uncited_flagged = [
        {"sentence": s, "reason": "factual-sounding claim with no [N] citation marker"}
        for i, s, refs in all_with_markers
        if not refs and _CLAIM_SIGNAL_RE.search(s)
    ]

    if not cited:
        return {"uncited_claims": uncited_flagged} if uncited_flagged else {}

    # Only send evidence for papers actually cited by these sentences — keeps
    # the prompt small and keeps the model from "finding a home" for a claim
    # among papers that weren't cited for it.
    used_indices = sorted(
        {idx for _, _, refs in cited for idx in refs},
        key=lambda x: int(x) if x.isdigit() else 0,
    )
    evidence_text = "\n\n".join(
        _evidence_block(idx, references_mapping[idx])
        for idx in used_indices
        if idx in references_mapping
    )
    sentence_list = "\n".join(f"{i}: {s}" for i, s, _ in cited)

    user_prompt = (
        "Cited sentences to verify (id: sentence text — verify each against the "
        "evidence for the paper number(s) shown in its own [N] marker):\n\n"
        f"{sentence_list}\n\n"
        "Evidence for the cited papers:\n\n"
        f"{evidence_text}\n\n"
        "Return the JSON verdict for every sentence id listed above."
    )

    verdicts: dict[int, dict] = {}
    try:
        raw = await generate_completion(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=1200,
            temperature=0.0,
            provider_override="groq",
        )
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found in response")
        parsed = json.loads(raw[start:end])
        verdicts = {
            r["sentence_id"]: r for r in parsed.get("results", []) if "sentence_id" in r
        }
    except Exception as e:
        logger.warning(f"Citation grounding check failed, marking citations unverified: {e}")
        # Fail-CLOSED here, unlike relevance.py's fail-open filter: a grounding
        # check that couldn't run must not silently report everything as fine.

    citation_map = []
    for i, s, refs in cited:
        v = verdicts.get(i, {})
        citation_map.append({
            "sentence": s,
            "cites": refs,
            "status": v.get("status", "unverified"),
            "note": v.get("note") or ("" if verdicts else "grounding check unavailable"),
        })

    result: dict = {"citation_map": citation_map}
    if uncited_flagged:
        result["uncited_claims"] = uncited_flagged
    return result
