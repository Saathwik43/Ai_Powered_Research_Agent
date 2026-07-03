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
import logging

logger = logging.getLogger(__name__)

__all__ = ["format_citation", "format_all_citations"]

# Aliases — "oxford" maps to chicago for reference-list formatting.
_STYLE_ALIASES = {"oxford": "chicago"}


def _parse_authors(raw: str) -> list[str]:
    """
    Split a raw author string into individual names.

    Handles common formats:
      - "Smith, J., Jones, A."
      - "Smith et al."
      - "J. Smith and A. Jones"
      - "J. Smith, A. Jones, B. Lee"
    """
    if not raw or raw.strip().lower() in ("unknown", "unknown authors", ""):
        return []
    # Split on common separators
    parts = re.split(r"\s*(?:,\s*and\s+|,\s*&\s*|\s+and\s+|\s*;\s*|,\s+(?=[A-Z]))", raw.strip())
    return [p.strip() for p in parts if p.strip() and p.strip() != "et al."]


def _initials_from_name(name: str) -> str:
    """Extract initials: 'John Smith' -> 'J. Smith', already-initialized pass through."""
    parts = name.strip().split()
    if len(parts) <= 1:
        return name
    # If last part looks like a last name (>= 2 chars, starts upper), keep it
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
    """
    IEEE style:
    A. Author and B. Author, "Article title," Journal, vol. V, no. N, Year. doi: DOI
    """
    authors = _parse_authors(paper.get("authors", ""))
    if authors:
        formatted = [_initials_from_name(a) for a in authors]
        if len(formatted) > 6: # typically et al. for >=7 in IEEE, but sticking to 3 for brevity is common in some variants, let's use standard IEEE which lists up to 6
            author_str = ", ".join(formatted[:3]) + " et al."
        elif len(formatted) > 1:
            author_str = ", ".join(formatted[:-1]) + ", and " + formatted[-1]
        else:
            author_str = formatted[0]
    else:
        author_str = "Unknown"

    title = paper.get("title", "Untitled")
    year = paper.get("year", "")

    parts = [f'{author_str}, "{title},"']

    # Journal / venue (if available)
    journal = paper.get("journal", paper.get("venue", ""))
    if journal:
        parts.append(f" {journal},")

    if year:
        parts.append(f" {year}.")
    else:
        # Replace trailing comma with period
        parts[-1] = parts[-1].rstrip(",") + "."

    doi = paper.get("doi", "")
    url = paper.get("url", "")
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f" doi: {doi_str}")
    elif url:
        parts.append(f" [Online]. Available: {url}")

    return "".join(parts)


# ─── APA 7th ───────────────────────────────────────────────────────────────────

def _last_initials(name: str) -> str:
    """Convert 'John A. Smith' -> 'Smith, J. A.' for APA."""
    parts = name.strip().split()
    if len(parts) <= 1:
        return name
    last = parts[-1]
    inits = " ".join(p[0].upper() + "." for p in parts[:-1])
    return f"{last}, {inits}"

def _format_apa(paper: dict) -> str:
    """
    APA 7th:
    Author, A. A., & Author, B. B. (Year). Article title. *Journal*, *V*(N), pp. doi
    """
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

    year = paper.get("year", "n.d.")
    title = paper.get("title", "Untitled")
    # APA: sentence case for article title (we keep as-is since we have real titles)

    parts = [f"{author_str} ({year}). {title}."]

    journal = paper.get("journal", paper.get("venue", ""))
    if journal:
        parts.append(f" *{journal}*.")

    doi = paper.get("doi", "")
    url = paper.get("url", "")
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f" {doi_str}")
    elif url:
        parts.append(f" {url}")

    return "".join(parts)


# ─── Chicago (Notes & Bibliography) ───────────────────────────────────────────

def _format_chicago(paper: dict) -> str:
    """
    Chicago / CMOS (bibliography entry):
    Author Last, First, and Second Author. "Article Title." *Journal* V, no. N (Year): pp. DOI.
    """
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

    title = paper.get("title", "Untitled")
    year = paper.get("year", "")

    author_part = f'{author_str}.' if not author_str.endswith('.') else author_str
    parts = [f'{author_part} "{title}."']

    journal = paper.get("journal", paper.get("venue", ""))
    if journal and year:
        parts.append(f" *{journal}* ({year}).")
    elif journal:
        parts.append(f" *{journal}*.")
    elif year:
        parts.append(f" ({year}).")

    doi = paper.get("doi", "")
    url = paper.get("url", "")
    if doi:
        doi_str = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        parts.append(f" {doi_str}.")
    elif url:
        parts.append(f" {url}.")

    return "".join(parts)


# ─── Public API ────────────────────────────────────────────────────────────────

_FORMATTERS = {
    "ieee": _format_ieee,
    "apa": _format_apa,
    "chicago": _format_chicago,
}


def format_citation(paper: dict, style: str = "ieee") -> str:
    """
    Format a single paper dict into a citation string.

    Parameters
    ----------
    paper : dict
        Paper metadata with keys like `title`, `authors`, `year`, `doi`.
    style : str
        Citation style: `"ieee"`, `"apa"`, `"chicago"` (or `"oxford"`).

    Returns
    -------
    str
        Formatted citation string.
    """
    style = _STYLE_ALIASES.get(style.lower(), style.lower())
    formatter = _FORMATTERS.get(style)
    if not formatter:
        raise ValueError(f"Unsupported citation style: {style!r}. Choose from: {list(_FORMATTERS)}")
    return formatter(paper)


def format_all_citations(papers: list, style: str = "ieee") -> list[str]:
    """Format a list of papers into numbered citation strings."""
    return [format_citation(p, style) for p in papers]
