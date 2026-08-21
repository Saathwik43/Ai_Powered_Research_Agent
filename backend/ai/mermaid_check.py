"""
Structural sanity checks for Mermaid diagram blocks in generated/revised prose.

Why this exists: the revise path hands the model a whole section with a fenced
```mermaid block embedded in it and asks for the section back. The model sees
that fence as ordinary text -- nothing in the pipeline noticed if it came back
truncated, renamed, unbalanced or silently deleted. The frontend then tried to
render it and the user got a broken chart *after* accepting the revision.

This is deliberately a cheap structural check, not a Mermaid parser. It mirrors
the shapes `frontend/src/utils/mermaidChart.js` knows how to render plus the
`xychart-beta` rules `_prompt()` asks the generator to follow, and it only
reports what it is confident about -- a validator that cries wolf trains the
user to ignore the one warning that mattered (see audit finding C2).
"""

import re

# Fence languages the renderer treats as Mermaid (mirrors MERMAID_LANGS in
# frontend/src/utils/mermaidChart.js).
MERMAID_LANGS = {
    "mermaid", "flowchart", "graph", "xychart-beta", "xychart", "gantt",
    "classdiagram", "pie", "sequencediagram", "statediagram", "statediagram-v2",
    "erdiagram", "mindmap", "timeline", "journey", "quadrantchart", "gitgraph",
    "sankey-beta", "block-beta", "requirementdiagram", "c4context",
}

# Valid first tokens of a diagram body (mirrors DIAGRAM_STARTERS frontend-side).
DIAGRAM_STARTERS = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram-v2",
    "stateDiagram", "erDiagram", "gantt", "pie", "journey", "mindmap", "timeline",
    "quadrantChart", "xychart-beta", "xychart", "gitGraph", "C4Context",
    "sankey-beta", "requirementDiagram", "block-beta",
)

_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_SERIES_RE = re.compile(r"^\s*(bar|line)\s*\[([^\]]*)\]", re.MULTILINE)
_XAXIS_RE = re.compile(r"^\s*x-axis\s*\[([^\]]*)\]", re.MULTILINE)
_PIE_SLICE_RE = re.compile(r'^\s*"[^"]*"\s*:\s*[\d.]+', re.MULTILINE)


def _starts_with_diagram(chart: str) -> bool:
    return any(
        chart == k or chart.startswith((f"{k} ", f"{k}\n", f"{k}-"))
        for k in DIAGRAM_STARTERS
    )


def is_mermaid_block(language: str, body: str) -> bool:
    lang = (language or "").strip().lower()
    body = (body or "").strip()
    if not body:
        return False
    if lang in MERMAID_LANGS or lang.startswith("mermaid"):
        return True
    return _starts_with_diagram(body)


def extract_mermaid_blocks(text: str) -> list:
    """Bodies of every fenced block the renderer would hand to Mermaid."""
    return [
        body.strip()
        for language, body in _FENCE_RE.findall(text or "")
        if is_mermaid_block(language, body)
    ]


def has_unclosed_fence(text: str) -> bool:
    """An odd number of fences means the last block never terminated."""
    return (text or "").count("```") % 2 == 1


def _split_values(raw: str) -> list:
    return [v.strip() for v in raw.split(",") if v.strip()]


def _check_xychart(chart: str) -> str:
    series = _SERIES_RE.findall(chart)
    if not series:
        return "xychart block has no `bar [...]` or `line [...]` data series"

    lengths = set()
    for kind, raw in series:
        values = _split_values(raw)
        if not values:
            return f"the `{kind}` series is empty"
        for value in values:
            try:
                float(value)
            except ValueError:
                # The generator is told to use plain numeric arrays; a stray
                # label or unit here is what makes the chart fail to render.
                return f"the `{kind}` series contains a non-numeric value ({value!r})"
        lengths.add(len(values))

    axis = _XAXIS_RE.search(chart)
    if axis:
        labels = len(_split_values(axis.group(1)))
        mismatched = sorted(n for n in lengths if n != labels)
        if labels and mismatched:
            return (
                f"x-axis has {labels} labels but a data series has "
                f"{mismatched[0]} values"
            )
    return ""


def validate_mermaid(chart: str) -> str:
    """Return a human-readable reason the chart will not render, or ""."""
    chart = (chart or "").strip()
    if not chart:
        return "the diagram block is empty"

    if not _starts_with_diagram(chart):
        first = chart.splitlines()[0].strip()[:40]
        return f"does not start with a diagram type (found {first!r})"

    for opener, closer in (("[", "]"), ("(", ")"), ("{", "}")):
        if chart.count(opener) != chart.count(closer):
            return f"unbalanced `{opener}` / `{closer}` — the block looks cut off"
    if chart.count('"') % 2:
        return "an unclosed double quote"

    if chart.startswith("xychart"):
        return _check_xychart(chart)
    if chart.startswith("pie") and not _PIE_SLICE_RE.search(chart):
        return 'pie chart has no `"Label": value` slices'
    return ""


def diagram_flags(previous: str, revised: str) -> dict:
    """
    Diagram health of *revised*, plus whether it lost diagrams *previous* had.

    Dropped diagrams are reported as a plain count, not an accusation: removing
    a chart is a legitimate thing to ask a revision for. The point is that the
    user sees it in the diff summary either way.
    """
    flags = {}
    errors = []

    if has_unclosed_fence(revised):
        errors.append({"index": None, "error": "a fenced code block was never closed"})

    for position, chart in enumerate(extract_mermaid_blocks(revised), 1):
        reason = validate_mermaid(chart)
        if reason:
            errors.append({"index": position, "error": reason})

    if errors:
        flags["diagram_errors"] = errors

    dropped = len(extract_mermaid_blocks(previous)) - len(extract_mermaid_blocks(revised))
    if dropped > 0:
        flags["diagrams_dropped"] = dropped

    return flags
