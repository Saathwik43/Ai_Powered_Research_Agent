"""
ai/citation_format.py
---------------------
Deterministic citation formatting from paper metadata.
No LLM call — pure string templating.

Supported styles:
  - ieee   : [N] A. Author, B. Author, "Title," Journal, Year.
  - apa    : Author, A. A., & Author, B. B. (Year). Title. Journal.
  - chicago: Author Last, First, and B. Author. "Title." Journal (Year).

The `chicago` formatter also accepts `oxford` as an alias.
"""

import re
import html
import logging

logger = logging.getLogger(__name__)

__all__ = ["format_citation", "format_all_citations"]

# Aliases
_STYLE_ALIASES = {}


def _clean_str(text: str) -> str:
    """Unescape HTML entities like &gt;, &lt;, &amp; and strip whitespace."""
    if not text:
        return ""
    return html.unescape(str(text)).strip()


def _parse_authors(raw: str) -> list[str]:
    """Split a raw author string into individual names."""
    cleaned = _clean_str(raw)
    if not cleaned or cleaned.lower() in ("unknown", "unknown authors", ""):
        return []
    parts = re.split(r"\s*(?:,\s*and\s+|,\s*&\s*|\s+and\s+|\s*;\s*|,\s+(?=[A-Z]))", cleaned)
    return [p.strip() for p in parts if p.strip() and p.strip() != "et al."]


def _initials_from_name(name: str) -> str:
    """Extract initials: 'John Smith' -> 'J. Smith'."""
    parts = name.strip().split()
    if len(parts) <= 1:
        return name
    if len(parts[-1]) >= 2:
        inits = " ".join(p[0].upper() + "." if len(p) > 1 and not p.endswith(".") else p for p in parts[:-1])
        return f"{inits} {parts[-1]}"
    return name


def _last_first(name: str) -> str:
    """Convert 'John A. Smith' -> 'Smith, John A.' for APA/Chicago."""
    parts = name.strip().split()
    if len(parts) <= 1:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


# ─── IEEE ──────────────────────────────────────────────────────────────────────

def _format_ieee(paper: dict) -> str:
    authors = _parse_authors(paper.get("authors", ""))
    if authors:
        formatted = [_initials_from_name(a) for a in authors]
        if len(formatted) > 6:
            author_str = ", ".join(formatted[:3]) + " et al."
        elif len(formatted) > 1:
            author_str = ", ".join(formatted[:-1]) + ", and " + formatted[-1]
        else:
            author_str = formatted[0]
    else:
        author_str = "Unknown"

    title = _clean_str(paper.get("title", "Untitled"))
    year = _clean_str(paper.get("year", ""))

    parts = [f'{author_str}, "{title},"']

    journal = _clean_str(paper.get("journal", paper.get("venue", "")))
    if journal:
        parts.append(f" {journal},")

    if year:
        parts.append(f" {year}.")
    else:
        parts[-1] = parts[-1].rstrip(",") + "."

    doi = _clean_str(paper.get("doi", ""))
    url = _clean_str(paper.get("url", ""))
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f" doi: {doi_str}")
    elif url:
        parts.append(f" [Online]. Available: {url}")

    return "".join(parts)


# ─── APA 7th ───────────────────────────────────────────────────────────────────

def _last_initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) <= 1:
        return name
    last = parts[-1]
    inits = " ".join(p[0].upper() + "." for p in parts[:-1])
    return f"{last}, {inits}"

def _format_apa(paper: dict) -> str:
    authors = _parse_authors(paper.get("authors", ""))
    if authors:
        formatted = [_last_initials(a) for a in authors]
        if len(formatted) > 7:
            author_str = ", ".join(formatted[:6]) + ", ... " + formatted[-1]
        elif len(formatted) >= 2:
            author_str = ", ".join(formatted[:-1]) + ", & " + formatted[-1]
        else:
            author_str = formatted[0]
    else:
        author_str = "Unknown"

    year = _clean_str(paper.get("year", "n.d."))
    title = _clean_str(paper.get("title", "Untitled"))

    parts = [f"{author_str} ({year}). {title}."]

    journal = _clean_str(paper.get("journal", paper.get("venue", "")))
    if journal:
        parts.append(f" *{journal}*.")

    doi = _clean_str(paper.get("doi", ""))
    url = _clean_str(paper.get("url", ""))
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f" {doi_str}")
    elif url:
        parts.append(f" {url}")

    return "".join(parts)


# ─── Chicago ──────────────────────────────────────────────────────────────────

def _format_chicago(paper: dict) -> str:
    authors = _parse_authors(paper.get("authors", ""))
    if authors:
        first = _last_first(authors[0])
        if len(authors) > 3:
            author_str = first + ", et al."
        elif len(authors) >= 2:
            rest = [a for a in authors[1:]]
            author_str = first + ", and " + ", and ".join(rest) if len(rest) == 1 else first + ", " + ", and ".join(rest)
        else:
            author_str = first
    else:
        author_str = "Unknown"

    title = _clean_str(paper.get("title", "Untitled"))
    year = _clean_str(paper.get("year", ""))

    author_part = f'{author_str}.' if not author_str.endswith('.') else author_str
    parts = [f'{author_part} "{title}."']

    journal = _clean_str(paper.get("journal", paper.get("venue", "")))
    if journal and year:
        parts.append(f" *{journal}* ({year}).")
    elif journal:
        parts.append(f" *{journal}*.")
    elif year:
        parts.append(f" ({year}).")

    doi = _clean_str(paper.get("doi", ""))
    url = _clean_str(paper.get("url", ""))
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f" {doi_str}.")
    elif url:
        parts.append(f" {url}.")

    return "".join(parts)


# ─── Oxford ───────────────────────────────────────────────────────────────────

def _format_oxford(paper: dict) -> str:
    authors = _parse_authors(paper.get("authors", ""))
    if authors:
        formatted = [_initials_from_name(a) for a in authors]
        if len(formatted) > 3:
            author_str = formatted[0] + " et al."
        elif len(formatted) >= 2:
            author_str = ", ".join(formatted[:-1]) + " and " + formatted[-1]
        else:
            author_str = formatted[0]
    else:
        author_str = "Unknown"

    title = _clean_str(paper.get("title", "Untitled"))
    year = _clean_str(paper.get("year", ""))
    journal = _clean_str(paper.get("journal", paper.get("venue", "")))

    parts = [f'{author_str}, "{title}"']
    if journal:
        parts.append(f', *{journal}*')
    if year:
        parts.append(f' ({year})')

    doi = _clean_str(paper.get("doi", ""))
    url = _clean_str(paper.get("url", ""))
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f', {doi_str}')
    elif url:
        parts.append(f', {url}')

    return "".join(parts) + "."


# ─── Public API ────────────────────────────────────────────────────────────────

_FORMATTERS = {
    "ieee": _format_ieee,
    "apa": _format_apa,
    "chicago": _format_chicago,
    "oxford": _format_chicago,
}


def format_citation(paper: dict, style: str = "ieee") -> str:
    style = _STYLE_ALIASES.get(style.lower(), style.lower())
    formatter = _FORMATTERS.get(style, _format_ieee)
    return formatter(paper)


def format_all_citations(papers: list, style: str = "ieee") -> list[str]:
    """Format a list of papers into numbered citation strings."""
    return [format_citation(p, style) for p in papers]

