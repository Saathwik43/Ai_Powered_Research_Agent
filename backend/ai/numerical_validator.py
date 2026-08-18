"""
ai/numerical_validator.py
-------------------------
Flags numbers in a generated section that cannot be traced back to the source
material the writer LLM was actually shown.

The corpus this checks against must match the corpus the prompt was built from.
`_prepare_generation` renders each reference from its six-field ``evidence``
dict whenever evidence exists and only falls back to ``abstract`` when it does
not -- so checking ``abstract`` alone reported correctly-sourced figures as
hallucinations. See EVIDENCE_FIELDS below.
"""

import re

KEYWORDS = [
    "efficiency", "accuracy", "yield", "rate", "precision",
    "recall", "f1", "factor", "ratio", "score", "capacity",
    "voltage", "current", "bandwidth", "frequency", "temperature",
    "power", "density", "conductance", "mobility", "viscosity",
    "solubility", "resolution", "error", "p-value", "coefficient"
]

# Same field list ai/evidence_extraction.py extracts and
# ai/manuscript_generation.py renders into the numbered reference list.
EVIDENCE_FIELDS = ("objective", "method", "dataset", "results", "limitations", "future_work")

_UNITS = r"nm|mA/cm²|eV|V|W|Hz|μm|mm|cm|m|mg|g|kg|s|min|h|dB|F|Pa|°C|K|M|mM|µM|L|mL|µL"
_PERCENT_RE = re.compile(r'\b(\d+(?:\.\d+)?)\s*(%|percent)', re.IGNORECASE)
_UNIT_RE = re.compile(r'\b(\d+(?:\.\d+)?)\s*(' + _UNITS + r')\b')
_NUMBER_RE = re.compile(r'\b(\d+(?:\.\d+)?)\b')

# [1] / [2, 3] reference markers are numbering, not claims. Left in place they
# were matched as bare numbers and flagged whenever a KEYWORD happened to sit
# within the +/-40 char window -- which, in a cited sentence, it usually does.
_CITATION_MARKER_RE = re.compile(r'\[\s*\d+(?:\s*[,-]\s*\d+)*\s*\]')

# Numbers describing a *proposed* experimental setup are not claims about the
# literature, so there is nothing in the sources for them to match. The prompt
# explicitly asks for methodology and projected outcomes (see
# _METHOD_RESULTS_FRAMING), so flagging these punished the model for complying.
_SETUP_CONTEXT_RE = re.compile(
    r"\b(epochs?|learning[- ]rate|lr|batch(?:[- ]size)?|seed|layers?|"
    r"hidden|dimension|dropout|momentum|weight[- ]decay|"
    r"k[- ]fold|folds?|split|train(?:ing)?[- ]set|test[- ]set|"
    r"hyper[- ]?parameters?|iterations?|steps?|warm[- ]?up)\b",
    re.IGNORECASE,
)


def _paper_text(paper: dict) -> str:
    """Every piece of a paper the writer LLM could have drawn a number from."""
    parts = [paper.get("title"), paper.get("abstract"), paper.get("text")]

    evidence = paper.get("evidence")
    if isinstance(evidence, dict):
        parts.extend(evidence.get(field) for field in EVIDENCE_FIELDS)
    elif isinstance(evidence, str):
        parts.append(evidence)

    # `or ""` rather than .get(k, ""): several integrations set the key to a
    # literal None (JSON null), which used to raise TypeError on concatenation
    # and surfaced to the user as a bogus 503 "AI temporarily unavailable".
    return " ".join(str(p) for p in parts if p)


def validate_numerical_claims(generated_text: str, source_papers: list) -> dict:
    """
    Validates numerical claims in generated_text against the text of source_papers.
    Returns a dictionary with 'unverified_numbers' listing any hallucinated stats.
    """
    if not source_papers or not generated_text:
        return {"unverified_numbers": []}

    source_text_lower = " ".join(_paper_text(p) for p in source_papers).lower()

    # Blank out reference markers so their digits can't be read as claims. Same
    # length replacement keeps every other span offset valid.
    scan_text = _CITATION_MARKER_RE.sub(lambda m: " " * len(m.group(0)), generated_text)

    extracted_claims = []
    percentage_ranges = [m.span() for m in _PERCENT_RE.finditer(scan_text)]
    unit_ranges = [m.span() for m in _UNIT_RE.finditer(scan_text)]

    for match in _PERCENT_RE.finditer(scan_text):
        extracted_claims.append((match.group(1), match.group(0).strip(), "percentage"))

    for match in _UNIT_RE.finditer(scan_text):
        extracted_claims.append((match.group(1), match.group(0).strip(), "unit"))

    for match in _NUMBER_RE.finditer(scan_text):
        span = match.span()
        # skip if this number overlaps with a percentage or unit
        if any(s[0] <= span[0] and span[1] <= s[1] for s in percentage_ranges + unit_ranges):
            continue

        start = max(0, match.start() - 40)
        end = min(len(scan_text), match.end() + 40)
        window = scan_text[start:end]

        if _SETUP_CONTEXT_RE.search(window):
            continue

        if any(kw in window.lower() for kw in KEYWORDS):
            extracted_claims.append((match.group(1), match.group(0).strip(), "bare"))

    unverified = []
    seen = set()

    for num_val, original_str, ctype in extracted_claims:
        if ctype == "percentage":
            pattern = r'\b' + re.escape(num_val) + r'\s*(?:%|percent)'
        elif ctype == "unit":
            unit_part = original_str[len(num_val):].strip().lower()
            pattern = r'\b' + re.escape(num_val) + r'\s*' + re.escape(unit_part)
        else:
            pattern = r'\b' + re.escape(num_val) + r'\b'

        if re.search(pattern, source_text_lower):
            continue

        if original_str not in seen:
            seen.add(original_str)
            unverified.append(original_str)

    return {"unverified_numbers": unverified}
