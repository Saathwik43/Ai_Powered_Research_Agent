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
    # T1 text-mode < and > are inverted punctuation, not comparison glyphs (L8).
    "<": r"\textless{}",
    ">": r"\textgreater{}",
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

# Display titles for stored section keys. `.title()` on `lit_review` prints
# `Lit_Review` (L7); inner markdown headings are subsections of these.
_SECTION_TITLES = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related work": "Related Work",
    "background": "Background",
    "lit_review": "Literature Review",
    "literature_review": "Literature Review",
    "methodology": "Methodology",
    "method": "Methodology",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
    "acknowledgments": "Acknowledgments",
}

_TINY_KEYWORD_WORDS = {
    "a", "an", "the", "of", "and", "or", "for", "to", "in", "on", "at",
    "by", "as", "vs", "via", "with", "from", "into", "over",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BOLD_HEADING_RE = re.compile(r"^\*\*(.+?)\*\*$")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$")

# `[1]`, `[1,2,8]` and the Markdown footnote form `[^4]` the generator emits in
# lit_review. Matched before escaping, while the `^` is still intact.
_CITATION_RE = re.compile(r"\[\^?\s*\d+(?:\s*,\s*\^?\s*\d+)*\s*\]")

# Closed fences first; a truncated generation may leave an opener with no close.
_CLOSED_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"```.*", re.DOTALL)


def _normalize_tex_delimiters(text: str) -> str:
    """Turn LaTeX `\\[...\\]` / `\\(...\\)` into `$` / `$$` before conversion.

    remark-math and this exporter both only treat dollar delimiters as math.
    `\\[` is also an escaped `[` in Markdown, so leaving it produces a literal
    `[ L_n = \\frac{...} ]` in the paper preview and `\\textbackslash{}[` in TeX.
    """
    fences = []

    def stash(match):
        fences.append(match.group(0))
        return f"@@FENCE{len(fences) - 1}@@"

    text = _CLOSED_FENCE_RE.sub(stash, text or "")
    text = re.sub(
        r"\\\[(.*?)\\\]",
        lambda m: "$$" + re.sub(r"\s+", " ", m.group(1)).strip() + "$$",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\\((.*?)\\\)",
        lambda m: "$" + re.sub(r"\s+", " ", m.group(1)).strip() + "$",
        text,
        flags=re.DOTALL,
    )
    for i, fence in enumerate(fences):
        text = text.replace(f"@@FENCE{i}@@", fence)
    return text


def _strip_fenced_blocks(text: str) -> str:
    """Drop mermaid/code fences so chart arrays are not read as citation lists.

    `xychart-beta` emits `bar [2, 6, 7, 8]`. That is the same shape as a grouped
    IEEE marker, and scanning it as citations is how a draft with a complete
    bibliography still refuses to export.
    """
    stripped = _CLOSED_FENCE_RE.sub(" ", text or "")
    return _OPEN_FENCE_RE.sub(" ", stripped)


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


def _cited_indices(content: dict) -> set:
    """`[N]` markers in prose. Fenced diagrams and the References section are ignored."""
    used = set()
    for name, body in (content or {}).items():
        if name.strip().lower() in _REFERENCE_SECTIONS:
            continue
        for match in _CITATION_RE.finditer(_strip_fenced_blocks(body or "")):
            used.update(re.findall(r"\d+", match.group(0)))
    return used


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
    used = _cited_indices(content)

    missing = sorted(used - known, key=int)
    if missing:
        markers = ", ".join(f"[{n}]" for n in missing)
        if not known:
            # Recoverable, and the recovery is not guessable: the draft predates
            # reference-snapshot persistence, so the numbering the writer LLM used
            # was never stored. Say so instead of listing 30 markers as if the
            # prose were at fault.
            raise ValueError(
                f"This draft cites {markers} but has no stored reference set, so the "
                "export would ship an empty references.bib and no \\cite commands. "
                "The exporter already tried to rebuild the set from the saved literature "
                "survey. Regenerate any one section to rebuild the reference set, then "
                "export again."
            )
        raise ValueError(
            "Citation markers with no matching reference: "
            + markers
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


def _demote_inline_display_math(text: str) -> str:
    """L6: `$$...$$` on a line that also has prose must be inline `$...$`."""
    stripped = text.strip()
    if stripped.startswith("$$") and stripped.endswith("$$") and stripped.count("$$") == 2:
        return text
    return re.sub(r"\$\$(.+?)\$\$", r"$\1$", text)


def _split_table_row(line: str) -> list:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _emit_table(rows: list, caption: str, cite_keys: dict) -> list:
    if not rows:
        return []
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    spec = "l" * width
    out = [r"\begin{table}[ht]", r"\centering"]
    if caption:
        out.append(r"\caption{" + caption + "}")
    out.append(r"\begin{tabular}{" + spec + "}")
    out.append(r"\hline")
    for i, row in enumerate(padded):
        cells = [_inline_md(cell, cite_keys) for cell in row]
        out.append(" & ".join(cells) + r" \\")
        if i == 0:
            out.append(r"\hline")
    out.append(r"\hline")
    out.append(r"\end{tabular}")
    out.append(r"\end{table}")
    return out


def _close_list(out: list, in_list: bool) -> bool:
    if in_list:
        out.append(r"\end{itemize}")
    return False


def _heading_command(hashes: int) -> str:
    # Already wrapped in \section{...}; never emit another \section here (L3).
    if hashes >= 3:
        return r"\subsubsection"
    return r"\subsection"


def _markdown_to_latex(md: str, cite_keys: dict = None) -> str:
    """
    Markdown -> LaTeX for what the generator actually emits: headings, pipe
    tables, lists, bold, citations, math, blockquotes, and mermaid fences
    (flagged, not converted).
    """
    md = _normalize_tex_delimiters(md)
    lines = (md or "").split("\n")
    out = []
    in_list = False
    in_mermaid = False
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```mermaid") or (
            stripped.startswith("```") and not in_mermaid and _looks_like_mermaid(stripped)
        ):
            in_list = _close_list(out, in_list)
            in_mermaid = True
            out.append("% TODO: Mermaid diagram omitted -- recreate as a LaTeX figure "
                        "(tikz/pgfplots or an exported image) before submission.")
            out.append("% --- original Mermaid source (for reference; does not compile) ---")
            i += 1
            continue
        if in_mermaid:
            if stripped.startswith("```"):
                in_mermaid = False
                out.append("% --- end Mermaid source ---")
            else:
                out.append("% " + line.rstrip("\r\n") if line.strip() else "%")
            i += 1
            continue

        if not stripped:
            in_list = _close_list(out, in_list)
            out.append("")
            i += 1
            continue

        if _HR_RE.match(stripped):
            in_list = _close_list(out, in_list)
            i += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            in_list = _close_list(out, in_list)
            cmd = _heading_command(len(heading.group(1)))
            out.append(f"{cmd}{{{_inline_md(heading.group(2), cite_keys)}}}")
            i += 1
            continue

        bold_heading = _BOLD_HEADING_RE.match(stripped)
        if bold_heading and stripped == f"**{bold_heading.group(1)}**":
            in_list = _close_list(out, in_list)
            out.append(rf"\subsection{{{_inline_md(bold_heading.group(1), cite_keys)}}}")
            i += 1
            continue

        if stripped.startswith(">"):
            in_list = _close_list(out, in_list)
            out.append(r"\begin{quote}")
            while i < n and lines[i].strip().startswith(">"):
                quoted = re.sub(r"^>\s?", "", lines[i].strip())
                if quoted:
                    out.append(_inline_md(quoted, cite_keys))
                i += 1
            out.append(r"\end{quote}")
            continue

        if _is_table_row(stripped) and i + 1 < n and (
            _TABLE_SEP_RE.match(lines[i + 1].strip()) or _is_table_row(lines[i + 1].strip())
        ):
            in_list = _close_list(out, in_list)
            caption = ""
            if out and out[-1].startswith(r"\textbf{"):
                caption = out.pop()[len(r"\textbf{"):-1]
            elif out and out[-1].startswith(r"\subsection{"):
                # A bold-as-heading immediately above a table is the caption.
                caption = out.pop()[len(r"\subsection{"):-1]
            rows = [_split_table_row(stripped)]
            i += 1
            if i < n and _TABLE_SEP_RE.match(lines[i].strip()):
                i += 1
            while i < n and _is_table_row(lines[i].strip()) and not _TABLE_SEP_RE.match(lines[i].strip()):
                rows.append(_split_table_row(lines[i].strip()))
                i += 1
            out.extend(_emit_table(rows, caption, cite_keys))
            continue

        bullet_match = re.match(r"^[-*]\s+(.*)", stripped)
        if bullet_match:
            if not in_list:
                out.append(r"\begin{itemize}")
                in_list = True
            out.append(r"\item " + _inline_md(bullet_match.group(1), cite_keys))
            i += 1
            continue

        in_list = _close_list(out, in_list)
        out.append(_inline_md(stripped, cite_keys))
        i += 1

    _close_list(out, in_list)
    return "\n".join(out)


def _looks_like_mermaid(fence_line: str) -> bool:
    # Bare ``` fences with no language tag aren't assumed to be mermaid;
    # only explicit ```mermaid is flagged. Kept as a separate helper in
    # case venue-specific detection needs to widen later.
    return False


def _inline_md(text: str, cite_keys: dict = None) -> str:
    """Citations + bold + escape, math passed through (escape already protects it)."""
    text = _demote_inline_display_math(text)
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


def _section_display_name(name: str) -> str:
    key = name.strip().lower().replace(" ", "_")
    spaced = name.strip().lower().replace("_", " ")
    if key in _SECTION_TITLES:
        return _SECTION_TITLES[key]
    if spaced in _SECTION_TITLES:
        return _SECTION_TITLES[spaced]
    return _title_case_heading(name.strip().replace("_", " "))


def _title_case_heading(text: str) -> str:
    """Title-case words, keeping all-caps tokens (CNN, MRI) intact."""
    parts = []
    for word in text.split():
        if len(word) > 1 and word.isupper():
            parts.append(word)
        else:
            parts.append(word[:1].upper() + word[1:].lower() if word else word)
    return " ".join(parts)


def _keywords_from_topic(topic: str) -> str:
    words = []
    for raw in re.split(r"[\s,;/]+", topic or ""):
        token = raw.strip()
        if not token or token.lower() in _TINY_KEYWORD_WORDS:
            continue
        cleaned = re.sub(r"[^A-Za-z0-9\-]+", "", token)
        if cleaned:
            words.append(cleaned)
    return ", ".join(words[:8])


def match_manuscript_refs_to_papers(manuscript_refs, papers, citation_style: str = "ieee") -> list:
    """Rebuild a `[N]` snapshot from display citations + cached papers (L14).

    Returns paper dicts with ``index`` set, same shape the LaTeX exporter needs.
    Dict-valued ``manuscript_refs`` are used as-is. String values are matched
    against ``format_citation`` or a title substring.
    """
    from ai.citation_format import format_citation

    if not manuscript_refs:
        return []

    unused = [p for p in (papers or []) if isinstance(p, dict)]
    snapshot = []

    if isinstance(manuscript_refs, list):
        items = [(str(i + 1), v) for i, v in enumerate(manuscript_refs)]
    elif isinstance(manuscript_refs, dict):
        items = list(manuscript_refs.items())
    else:
        return []

    for raw_idx, value in items:
        index = str(raw_idx)
        if isinstance(value, dict) and (value.get("title") or value.get("doi") or value.get("authors")):
            snapshot.append({"index": index, **{k: v for k, v in value.items() if k != "index"}})
            continue
        text = str(value or "").strip()
        if not text:
            continue
        text_l = text.lower()
        found_i = None
        for i, paper in enumerate(unused):
            title = (paper.get("title") or "").strip()
            formatted = format_citation(paper, style=citation_style) or ""
            if title and title.lower() in text_l:
                found_i = i
                break
            if formatted and (formatted.lower() in text_l or text_l in formatted.lower()):
                found_i = i
                break
        if found_i is None:
            # Keep the index even when the survey cache cannot resolve a paper.
            # Dropping it is how a 15-item citation list became 11 snapshot
            # rows and export then refused the markers still in the prose.
            snapshot.append({
                "index": index,
                "title": text[:500],
                "authors": "",
                "year": "",
                "journal": "",
                "doi": "",
                "url": "",
                "from_display_only": True,
            })
            continue
        paper = unused.pop(found_i)
        snapshot.append({
            "index": index,
            "title": paper.get("title") or "Unknown Title",
            "authors": paper.get("authors") or "",
            "year": paper.get("year") or "",
            "journal": paper.get("journal") or paper.get("venue") or paper.get("source") or "",
            "doi": paper.get("doi") or "",
            "url": paper.get("url") or "",
            "abstract": (paper.get("abstract") or "")[:600],
        })
    snapshot.sort(key=lambda p: int(p["index"]) if str(p.get("index", "")).isdigit() else 0)
    return snapshot


def fill_reference_holes(references, manuscript_refs, *, used_indices=None,
                         papers=None, citation_style: str = "ieee") -> list:
    """Reattach display-list indices the stored snapshot dropped.

    Existing snapshot rows win, so a real paper is never replaced by a stub.
    When *used_indices* is set, only cited holes are filled — unused unmatched
    strings stay out of references.bib rather than showing up as uncited.
    """
    merged = {}
    for i, paper in enumerate(references or [], 1):
        if not isinstance(paper, dict):
            continue
        merged[str(paper.get("index") or i)] = paper
    if not manuscript_refs:
        return [merged[k] for k in sorted(merged, key=lambda n: int(n) if n.isdigit() else 0)]

    needed = None if used_indices is None else {str(n) for n in used_indices} - set(merged)
    if needed == set():
        return [merged[k] for k in sorted(merged, key=lambda n: int(n) if n.isdigit() else 0)]

    for paper in match_manuscript_refs_to_papers(manuscript_refs, papers, citation_style):
        idx = str(paper.get("index") or "")
        if not idx or idx in merged:
            continue
        if needed is not None and idx not in needed:
            continue
        merged[idx] = paper
    return [merged[k] for k in sorted(merged, key=lambda n: int(n) if n.isdigit() else 0)]


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
        parts.append(f"\\section{{{_latex_escape(_section_display_name(name))}}}")
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
    manuscript_refs=None,
) -> tuple:
    """
    Returns ``(tex_str, bib_str, warnings)`` for the requested venue.

    topic: manuscript topic, used as the paper title if no explicit title
    content: {section_name: markdown_body} as stored by /api/manuscript/save
    venue: one of VENUES keys ("ieee", "acm", "springer", "elsevier")
    references: list of paper dicts, each carrying the ``index`` its `[N]`
        markers use (title/authors/year/journal/doi/url)
    manuscript_refs: optional display list ({index: formatted string or dict})
        used to fill snapshot holes so a partial rebuild does not refuse
        markers the writer actually emitted

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
    references = fill_reference_holes(
        references or [],
        manuscript_refs,
        used_indices=_cited_indices(content),
    )
    stub_idx = sorted(
        (
            str(p.get("index") or i)
            for i, p in enumerate(references, 1)
            if isinstance(p, dict) and p.get("from_display_only")
        ),
        key=lambda n: int(n) if n.isdigit() else 0,
    )
    if stub_idx:
        warnings.append(
            "Bibliography entries taken from the saved citation list "
            "(title/DOI may be incomplete): "
            + ", ".join(f"[{n}]" for n in stub_idx)
        )
    cite_keys = _cite_key_map(references)
    # Deliberately NOT guarded by `if references:`. An empty reference set is the
    # worst case, not the harmless one -- it is what a draft written before
    # snapshot persistence produces, and skipping the check there is how a zip
    # with a zero-byte references.bib shipped while a single stray [99] among
    # real references was still caught (audit L10).
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

    if not (keywords or "").strip():
        keywords = _keywords_from_topic(topic)

    tex = (
        skeleton
        .replace("{{TITLE}}", _latex_escape(_title_case_heading(topic)))
        .replace("{{AUTHOR_NAME}}", _latex_escape(author_name))
        .replace("{{AUTHOR_AFFIL}}", _latex_escape(author_affil))
        .replace("{{ABSTRACT}}", abstract_body)
        .replace("{{KEYWORDS}}", _latex_escape(keywords))
        .replace("{{SECTIONS}}", sections_latex)
    )

    bib = _build_bibtex(references or [])

    return tex, bib, warnings
