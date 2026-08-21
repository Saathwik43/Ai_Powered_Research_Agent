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

# Characters that must be escaped in LaTeX text mode.
#
# `\`, `^` and `~` were missing, and their absence was fatal rather than
# cosmetic: the manuscript generator emits Markdown footnote citations
# (`[^1]` ... `[^15]`, 30+ per lit_review), each of which put a bare `^` in text
# mode and made the whole document fail with `! Missing $ inserted`. `~` was
# quieter and worse -- it silently became a non-breaking space. See audit L1.
#
# `$` is here for *unmatched* dollars only; balanced math is stashed out before
# escaping and restored afterwards.
_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}

# One pass, so a replacement can never be re-escaped by a later one. The old
# sequential loop could not express this: `\` -> `\textbackslash{}` emits braces
# and a backslash, which the `{`/`}` passes would then mangle.
_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _ESCAPE_MAP))

# Characters with no T1 glyph, which survive `inputenc` only to fail at the
# font. Rewritten to math before escaping, so the inserted `$...$` is picked up
# by the math stash and passed through untouched. See audit L5.
_UNICODE_MATH = {
    "×": r"$\times$", "÷": r"$\div$", "−": r"$-$",
    "≈": r"$\approx$", "≤": r"$\leq$", "≥": r"$\geq$",
    "≠": r"$\neq$", "∞": r"$\infty$", "µ": r"$\mu$",
    "μ": r"$\mu$", "α": r"$\alpha$", "β": r"$\beta$",
    "γ": r"$\gamma$", "δ": r"$\delta$", "λ": r"$\lambda$",
    "σ": r"$\sigma$", "Ω": r"$\Omega$", "∆": r"$\Delta$",
}
# Subscript/superscript digits: common in chemistry the generator emits raw
# (SiO₂, CO₂), and pdfLaTeX has no glyph for any of them.
for _i, _d in enumerate("₀₁₂₃₄₅₆₇₈₉"):
    _UNICODE_MATH[_d] = f"$_{_i}$"
for _i, _d in enumerate("⁰¹²³⁴⁵⁶⁷⁸⁹"):
    _UNICODE_MATH[_d] = f"$^{_i}$"

_UNICODE_RE = re.compile("|".join(re.escape(c) for c in _UNICODE_MATH))

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

# Sections that duplicate what BibTeX produces. Dropped when we have a real
# reference set, otherwise the export ships a hand-numbered plain-text list in
# the wrong style *and* an auto bibliography. See audit L4.
_REFERENCE_SECTIONS = {"references", "reference", "bibliography", "works cited"}

# `[1]`, `[1,2,8]` and the Markdown footnote form `[^4]` the generator emits in
# lit_review. Matched before escaping, while the `^` is still intact.
_CITATION_RE = re.compile(r"\[\^?\s*\d+(?:\s*,\s*\^?\s*\d+)*\s*\]")


def _cite_key_map(references: list) -> dict:
    """
    ``{"1": "liu20241", ...}`` keyed by the index the reference carries.

    Deliberately not `enumerate()`: `_reference_snapshot` persists the same
    numbering the writer LLM was given, so the mapping is a lookup, not an
    inference. Falling back to position would reintroduce exactly the drift
    C1 and C4 were about.
    """
    keys = {}
    for position, paper in enumerate(references or [], 1):
        index = str(paper.get("index") or position)
        keys[index] = _bibtex_key(paper, index)
    return keys


def _verify_citations(content: dict, references: list) -> list:
    """
    Check the prose numbering against the reference set.

    Returns the indices that exist but are never cited (a warning -- BibTeX
    silently drops them). Raises ValueError when the prose cites a number with
    no reference behind it, because once `[N]` becomes `\\cite{}` a mismatch
    stops being cosmetic: the document compiles cleanly and cites the wrong
    sources, with nothing visibly wrong.

    Deliberately NOT checked here: whether `[1]` is the *right* paper for a
    given claim. `check_citation_grounding` already answers that at generation
    and revision time; this only guarantees the export does not break it.
    """
    known = {str(p.get("index") or i) for i, p in enumerate(references or [], 1)}
    used = set()
    for name, body in (content or {}).items():
        if name.strip().lower() in _REFERENCE_SECTIONS:
            continue
        for match in _CITATION_RE.finditer(body or ""):
            used.update(re.findall(r"\d+", match.group(0)))

    missing = sorted(used - known, key=int)
    if missing:
        raise ValueError(
            "Citation markers with no matching reference: "
            + ", ".join(f"[{n}]" for n in missing)
            + f". The manuscript has {len(known)} reference(s). "
            "Regenerate the affected section, or remove the marker, before exporting."
        )
    return sorted(known - used, key=int)


def _citation_map_comment(references: list, keys: dict) -> str:
    """A human-checkable map of number -> key -> paper, as LaTeX comments."""
    if not keys:
        return ""
    by_index = {str(p.get("index") or i): p for i, p in enumerate(references or [], 1)}
    lines = ["% --- Citation map (check before submitting) ---"]
    for index in sorted(keys, key=lambda k: int(k) if k.isdigit() else 0):
        paper = by_index.get(index, {})
        title = (paper.get("title") or "Unknown Title")[:70]
        lines.append(f"% [{index}] -> {keys[index]}  {title}")
    lines.append("% --- end citation map ---")
    return "\n".join(lines)


def _latex_escape(text: str) -> str:
    """Escape LaTeX special characters in plain text (not inside math mode)."""
    # Rewrite glyph-less Unicode to math first, so the $...$ it produces is
    # protected by the stash below rather than escaped into literal dollars.
    text = _UNICODE_RE.sub(lambda m: _UNICODE_MATH[m.group(0)], text)

    # Protect math segments ($...$ and $$...$$) from escaping, then restore.
    math_segments = []

    def _stash(m):
        math_segments.append(m.group(0))
        return f"@@MATH{len(math_segments) - 1}@@"

    text = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", _stash, text, flags=re.DOTALL)

    text = _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(0)], text)

    for i, seg in enumerate(math_segments):
        text = text.replace(f"@@MATH{i}@@", seg)

    return text


def _markdown_to_latex(md: str, cite_keys: dict = None) -> str:
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
            out.append(r"\item " + _inline_md(bullet_match.group(1), cite_keys))
            continue

        if in_list:
            out.append(r"\end{itemize}")
            in_list = False

        out.append(_inline_md(stripped, cite_keys))

    if in_list:
        out.append(r"\end{itemize}")

    return "\n".join(out)


def _looks_like_mermaid(fence_line: str) -> bool:
    # Bare ``` fences with no language tag aren't assumed to be mermaid;
    # only explicit ```mermaid is flagged. Kept as a separate helper in
    # case venue-specific detection needs to widen later.
    return False


def _inline_md(text: str, cite_keys: dict = None) -> str:
    """Citations + bold + escape, math passed through (escape already protects it)."""
    # Citations are substituted before escaping -- `[^4]` still has its caret
    # here -- and stashed so the escaper cannot touch the \cite{} braces.
    stash = []
    if cite_keys:
        def _cite(match):
            keys = [cite_keys[n] for n in re.findall(r"\d+", match.group(0)) if n in cite_keys]
            if not keys:
                return match.group(0)
            stash.append("\\cite{" + ",".join(keys) + "}")
            return f"@@CITE{len(stash) - 1}@@"

        text = _CITATION_RE.sub(_cite, text)

    text = _latex_escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)

    for i, cite in enumerate(stash):
        text = text.replace(f"@@CITE{i}@@", cite)
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


def _build_sections_latex(content: dict, cite_keys: dict = None,
                          drop_reference_sections: bool = False) -> str:
    parts = []
    for name, body in _order_sections(content):
        key = name.strip().lower()
        if key == "abstract":
            continue  # abstract goes in the frontmatter block, not \section
        if drop_reference_sections and key in _REFERENCE_SECTIONS:
            continue  # BibTeX owns the list now
        parts.append(f"\\section{{{_latex_escape(name.strip().title())}}}")
        parts.append(_markdown_to_latex(body or "", cite_keys))
        parts.append("")
    return "\n".join(parts)


def _bibtex_key(paper: dict, index) -> str:
    authors_raw = paper.get("authors", "") or ""
    first_author = authors_raw.split(",")[0].strip() if authors_raw else ""
    last_name = first_author.split()[-1] if first_author else "ref"
    last_name = re.sub(r"[^a-zA-Z]", "", last_name) or "ref"
    year = re.sub(r"[^0-9]", "", str(paper.get("year", "")) or "")
    return f"{last_name}{year or index}{index}"


def _build_bibtex(references: list) -> str:
    entries = []
    for i, paper in enumerate(references or [], 1):
        # Key off the persisted index so the .bib and the \cite{} markers agree
        # by construction rather than by both happening to enumerate the same way.
        key = _bibtex_key(paper, str(paper.get("index") or i))
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
    Returns ``(tex_str, bib_str, warnings)`` for the requested venue.

    topic: manuscript topic, used as the paper title if no explicit title
    content: {section_name: markdown_body} as stored by /api/manuscript/save
    venue: one of VENUES keys ("ieee", "acm", "springer", "elsevier")
    references: list of paper dicts, each carrying the ``index`` its `[N]`
        markers use (title/authors/year/journal/doi/url)

    Raises ValueError when the prose cites a number with no reference behind it;
    the router surfaces that as a 400 rather than shipping a paper that cites
    the wrong sources.
    """
    venue = venue.lower()
    if venue not in VENUES:
        raise ValueError(f"Unknown venue '{venue}'. Supported: {list(VENUES.keys())}")

    template_path = os.path.join(_TEMPLATE_DIR, VENUES[venue]["file"])
    with open(template_path, "r", encoding="utf-8") as f:
        skeleton = f.read()

    warnings = []
    cite_keys = _cite_key_map(references)
    if references:
        uncited = _verify_citations(content, references)
        if uncited:
            warnings.append(
                "Not cited anywhere in the text, so BibTeX will omit them: "
                + ", ".join(f"[{n}]" for n in uncited)
            )

    abstract_body = ""
    for name, body in content.items():
        if name.strip().lower() == "abstract":
            abstract_body = _markdown_to_latex(body or "", cite_keys)
            break

    sections_latex = _build_sections_latex(
        content, cite_keys, drop_reference_sections=bool(references)
    )
    citation_map = _citation_map_comment(references, cite_keys)
    if citation_map:
        sections_latex = f"{citation_map}\n\n{sections_latex}"

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

    return tex, bib, warnings
