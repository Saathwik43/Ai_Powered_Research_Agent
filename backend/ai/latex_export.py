"""
ai/latex_export.py
-------------------
Converts a saved manuscript (section-name -> markdown text dict) into a
compilable .tex + .bib pair for a chosen publisher venue.

Scope (v1): section body conversion, reference list -> venue-correct .bib,
skeleton fill. Explicitly NOT handled here (documented, not silently
dropped):
  - Mermaid diagram blocks -> flagged as a TODO comment with the original
    Mermaid source preserved as LaTeX comments (not converted to a real
    figure). Mermaid doesn't compile in LaTeX.
  - ACM CCS Concepts / Elsevier Highlights -> left as placeholders; both
    need real classification/summarization, not string templating.
  - No PDF compilation. Output is .tex + .bib for Overleaf or a local
    TeX toolchain, matching how every publisher's own docs expect authors
    to work anyway.
"""

import os
import re
import logging

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "latex_templates")

VENUES = {
    "ieee": {"file": "ieee.tex", "bibstyle": "IEEEtran"},
    "acm": {"file": "acm.tex", "bibstyle": "ACM-Reference-Format"},
    "springer": {"file": "springer.tex", "bibstyle": "splncs04"},
    "elsevier": {"file": "elsevier.tex", "bibstyle": "elsarticle-num"},
}

# Canonical section order most venues expect. Sections present in the
# manuscript but not in this list are appended at the end, in whatever
# order they were stored, rather than dropped.
_SECTION_ORDER = [
    "introduction", "related work", "background", "methodology", "method",
    "results", "discussion", "conclusion", "acknowledgments",
]


def _latex_escape(text: str) -> str:
    """Escape LaTeX special characters in plain text (not inside math mode)."""
    # Protect math segments ($...$ and $$...$$) from escaping, then restore.
    math_segments = []

    def _stash(m):
        math_segments.append(m.group(0))
        return f"@@MATH{len(math_segments) - 1}@@"

    text = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", _stash, text, flags=re.DOTALL)

    replacements = {
        "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)

    for i, seg in enumerate(math_segments):
        text = text.replace(f"@@MATH{i}@@", seg)

    return text


def _markdown_to_latex(md: str) -> str:
    """
    Minimal, targeted markdown -> LaTeX conversion covering exactly what
    the manuscript generator actually produces (per manuscript_generation.py
    prompt rules): bold, bullet/numbered lists, paragraphs, inline/block
    math (already LaTeX syntax, passed through untouched), and mermaid
    code fences (flagged, not converted).
    """
    lines = md.split("\n")
    out = []
    in_list = False
    in_mermaid = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```mermaid") or (stripped.startswith("```") and not in_mermaid and _looks_like_mermaid(stripped)):
            in_mermaid = True
            out.append("% TODO: Mermaid diagram omitted -- recreate as a LaTeX figure "
                        "(tikz/pgfplots or an exported image) before submission.")
            out.append("% --- original Mermaid source (for reference; does not compile) ---")
            continue
        if in_mermaid:
            if stripped.startswith("```"):
                in_mermaid = False
                out.append("% --- end Mermaid source ---")
            else:
                # Preserve chart data as LaTeX comments so authors can recreate the figure.
                out.append("% " + line.rstrip("\r\n") if line.strip() else "%")
            continue

        if not stripped:
            if in_list:
                out.append(r"\end{itemize}")
                in_list = False
            out.append("")
            continue

        bullet_match = re.match(r"^[-*]\s+(.*)", stripped)
        if bullet_match:
            if not in_list:
                out.append(r"\begin{itemize}")
                in_list = True
            out.append(r"\item " + _inline_md(bullet_match.group(1)))
            continue

        if in_list:
            out.append(r"\end{itemize}")
            in_list = False

        out.append(_inline_md(stripped))

    if in_list:
        out.append(r"\end{itemize}")

    return "\n".join(out)


def _looks_like_mermaid(fence_line: str) -> bool:
    # Bare ``` fences with no language tag aren't assumed to be mermaid;
    # only explicit ```mermaid is flagged. Kept as a separate helper in
    # case venue-specific detection needs to widen later.
    return False


def _inline_md(text: str) -> str:
    """Bold + escape, math passed through (escape already protects it)."""
    text = _latex_escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    return text


def _order_sections(content: dict) -> list:
    ordered = []
    remaining = dict(content)
    for key in _SECTION_ORDER:
        for actual_key in list(remaining.keys()):
            if actual_key.strip().lower() == key:
                ordered.append((actual_key, remaining.pop(actual_key)))
    # anything left (custom/unrecognised section names) appended as-is
    for k, v in remaining.items():
        ordered.append((k, v))
    return ordered


def _build_sections_latex(content: dict) -> str:
    parts = []
    for name, body in _order_sections(content):
        if name.strip().lower() == "abstract":
            continue  # abstract goes in the frontmatter block, not \section
        title = name.strip().title()
        parts.append(f"\\section{{{_latex_escape(title)}}}")
        parts.append(_markdown_to_latex(body or ""))
        parts.append("")
    return "\n".join(parts)


def _bibtex_key(paper: dict, index: int) -> str:
    authors_raw = paper.get("authors", "") or ""
    first_author = authors_raw.split(",")[0].strip() if authors_raw else ""
    last_name = first_author.split()[-1] if first_author else "ref"
    last_name = re.sub(r"[^a-zA-Z]", "", last_name) or "ref"
    year = re.sub(r"[^0-9]", "", str(paper.get("year", "")) or "")
    return f"{last_name}{year or index}{index}"


def _build_bibtex(references: list) -> str:
    entries = []
    for i, paper in enumerate(references):
        key = _bibtex_key(paper, i)
        title = (paper.get("title", "Untitled") or "Untitled").replace("{", "").replace("}", "")
        authors_raw = paper.get("authors", "") or "Unknown"
        authors_bib = " and ".join(a.strip() for a in re.split(r",\s*(?:and\s+)?|\s+and\s+", authors_raw) if a.strip())
        year = paper.get("year", "n.d.") or "n.d."
        journal = paper.get("journal") or paper.get("venue") or paper.get("source") or ""
        doi = paper.get("doi", "")
        url = paper.get("url", "")

        fields = [f'  title = {{{title}}}', f'  author = {{{authors_bib}}}', f'  year = {{{year}}}']
        if journal:
            fields.append(f'  journal = {{{journal}}}')
        if doi:
            fields.append(f'  doi = {{{doi}}}')
        elif url:
            fields.append(f'  url = {{{url}}}')

        entries.append(f"@article{{{key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries)


def export_manuscript(
    topic: str,
    content: dict,
    venue: str,
    references: list,
    author_name: str = "Author Name",
    author_affil: str = "Affiliation, City, Country",
    keywords: str = "",
) -> tuple:
    """
    Returns (tex_str, bib_str) for the requested venue.

    topic: manuscript topic, used as the paper title if no explicit title
    content: {section_name: markdown_body} as stored by /api/manuscript/save
    venue: one of VENUES keys ("ieee", "acm", "springer", "elsevier")
    references: list of paper dicts (title/authors/year/journal/doi/url)
    """
    venue = venue.lower()
    if venue not in VENUES:
        raise ValueError(f"Unknown venue '{venue}'. Supported: {list(VENUES.keys())}")

    template_path = os.path.join(_TEMPLATE_DIR, VENUES[venue]["file"])
    with open(template_path, "r", encoding="utf-8") as f:
        skeleton = f.read()

    abstract_body = ""
    for name, body in content.items():
        if name.strip().lower() == "abstract":
            abstract_body = _markdown_to_latex(body or "")
            break

    sections_latex = _build_sections_latex(content)

    tex = (
        skeleton
        .replace("{{TITLE}}", _latex_escape(topic))
        .replace("{{AUTHOR_NAME}}", _latex_escape(author_name))
        .replace("{{AUTHOR_AFFIL}}", _latex_escape(author_affil))
        .replace("{{ABSTRACT}}", abstract_body)
        .replace("{{KEYWORDS}}", _latex_escape(keywords))
        .replace("{{SECTIONS}}", sections_latex)
    )

    bib = _build_bibtex(references or [])

    return tex, bib
