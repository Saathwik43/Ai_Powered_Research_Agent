"""Bibliographic records: entry types, DOI validation, and the export formats.

This is the one module that knows what a reference *is* (2.8). Before it, the
exporter inferred everything inline and got two things wrong on every run:

* **Every entry was ``@article``.** A conference paper, a book chapter and a
  preprint all came out as journal articles, which is wrong in the printed
  bibliography for any venue style that distinguishes them — and IEEE, ACM and
  Springer all do.
* **The journal name was the search engine.** ``_build_bibtex`` fell back to
  ``paper["source"]``, which holds "OpenAlex" / "Semantic Scholar" / "arXiv",
  so exports shipped ``journal = {OpenAlex}``. That is worse than an empty
  field: it looks like real metadata.

The integrations now emit ``type`` in Crossref's vocabulary, plus ``venue``,
``publisher``, ``volume``, ``issue``, ``pages`` and ``eprint`` where the API
offers them, and everything below reads that shared shape.

Three emitters, one record model: ``to_bibtex`` for the LaTeX export,
``to_ris`` for Zotero/Mendeley/EndNote import, and ``to_csl_json`` for anything
CSL-driven (Pandoc, Zotero's native format).
"""

import json
import re

__all__ = [
    "normalize_doi",
    "venue_of",
    "entry_type",
    "bibtex_key",
    "to_bibtex",
    "to_ris",
    "to_csl_json",
]


# A DOI is "10." + a 4-9 digit registrant, a slash, then a non-empty suffix.
# Anything else in the field is a URL fragment, a stray "n/a", or a title that
# landed in the wrong column -- all of which used to be printed as a DOI.
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
    "doi ",
)

# Names of the services we *search*, never of a venue a paper was published in.
# `_build_bibtex` used to fall back to `paper["source"]`, so these reached the
# printed bibliography as journal names.
_NOT_VENUES = {
    "openalex",
    "semantic scholar",
    "semanticscholar",
    "arxiv",
    "crossref",
    "pubmed",
    "doaj",
    "springer",
    "europe pmc",
    "europepmc",
    "unpaywall",
    "core",
    "base",
    "openreview",
    "aclanthology",
    "acl anthology",
    "zenodo",
    "google scholar",
    "unknown",
    "n/a",
    "none",
    "",
}

# Crossref's `type` vocabulary -> BibTeX entry type. The integrations normalise
# their own vocabularies into these terms so this mapping stays single-valued.
_TYPE_TO_ENTRY = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "book-chapter": "incollection",
    "book-section": "incollection",
    "book-part": "incollection",
    "reference-entry": "incollection",
    "dissertation": "phdthesis",
    "report": "techreport",
    "report-component": "techreport",
    "standard": "techreport",
    "posted-content": "misc",
    "preprint": "misc",
    "dataset": "misc",
    "component": "misc",
    "other": "misc",
}

# Venue-name fallback, used only when no source reported a type. Ordered: the
# first hit wins, so "conference" must be tested before generic words.
_CONFERENCE_MARKERS = (
    "proceedings",
    # Venue strings are routinely abbreviated ("Proc. of ICML", "Int. Conf. on
    # Robotics"), so the spelled-out words alone miss a large share of real
    # conference papers and silently file them as @article.
    "proc.",
    "proc ",
    "conference",
    "conf.",
    "conf ",
    "symposium",
    "symp.",
    "workshop",
    "congress",
    " meeting",
)

_BOOK_MARKERS = ("lecture notes", "handbook", "encyclopedia")

# BibTeX/LaTeX special characters in a *text* field. `doi` and `url` are left
# alone on purpose -- escaping an underscore inside a URL breaks the link, and
# every style file already passes those fields through verbatim.
_BIB_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_BIB_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _BIB_ESCAPE_MAP))

# "A. Smith, B. Jones et al." -> the individual names. `et al.` is dropped
# rather than emitted as an author: RIS and CSL have no way to express "and
# others", and a literal "et al." author is what makes Zotero show a person
# called Al.
#
# Every source here truncates to three authors and appends " et al.", so the
# marker arrives *attached to the last name* ("N. Parmar et al.") far more often
# than as a standalone entry. Matching only the standalone form is what
# produced `AU  - al, N. Parmar et` in the RIS file.
_AUTHOR_SPLIT_RE = re.compile(r",\s*(?:and\s+)?|\s+and\s+", re.IGNORECASE)
_ET_AL_RE = re.compile(r"^et\.?\s*al\.?$", re.IGNORECASE)
_TRAILING_ET_AL_RE = re.compile(r"[\s,]+et\.?\s*al\.?\s*$", re.IGNORECASE)


def _clean(value) -> str:
    return str(value).strip() if value is not None else ""


def _bib_escape(text: str) -> str:
    """Escape LaTeX specials in a .bib *text* field (title, author, journal)."""
    return _BIB_ESCAPE_RE.sub(lambda m: _BIB_ESCAPE_MAP[m.group(0)], text)


def normalize_doi(raw) -> str:
    """A bare, validated DOI, or ``""`` when the field does not hold one.

    Returning ``""`` for junk is the point: an unvalidated DOI field is how a
    bibliography ends up printing ``doi = {https://doi.org/n/a}``. Callers omit
    the field entirely when this returns empty.
    """
    doi = _clean(raw)
    if not doi:
        return ""
    lowered = doi.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            doi = doi[len(prefix):].strip()
            break
    # DOIs are case-insensitive; the registrant prefix is conventionally lower
    # and the suffix is preserved as registered.
    return doi if _DOI_RE.match(doi) else ""


def venue_of(paper: dict) -> str:
    """The journal or proceedings name, never the name of a search service."""
    for key in ("journal", "venue", "container_title"):
        value = _clean(paper.get(key))
        if value and value.lower() not in _NOT_VENUES:
            return value
    return ""


def _is_preprint(paper: dict) -> bool:
    if _clean(paper.get("subtype")).lower() == "preprint":
        return True
    if _clean(paper.get("type")).lower() in ("posted-content", "preprint"):
        return True
    # An arXiv id and no venue is a preprint whatever the record claims.
    return bool(_clean(paper.get("eprint"))) and not venue_of(paper)


def entry_type(paper: dict) -> str:
    """The BibTeX entry type for *paper*.

    Order matters. A reported type beats an inferred one, the host-venue kind
    beats the venue's name, and the name is read only when nothing else spoke.
    ``article`` is the default because an unlabelled record in this corpus is
    overwhelmingly a journal paper -- and because it is the type every style
    file renders sanely.
    """
    if _is_preprint(paper):
        return "misc"

    reported = _TYPE_TO_ENTRY.get(_clean(paper.get("type")).lower())
    if reported:
        return reported

    venue_type = _clean(paper.get("venue_type")).lower()
    if venue_type == "conference":
        return "inproceedings"
    if venue_type in ("book series", "book"):
        return "incollection"
    if venue_type == "journal":
        return "article"
    if venue_type == "repository":
        return "misc"

    venue = venue_of(paper).lower()
    if venue:
        if any(marker in venue for marker in _CONFERENCE_MARKERS):
            return "inproceedings"
        if any(marker in venue for marker in _BOOK_MARKERS):
            return "incollection"

    return "article"


def bibtex_key(paper: dict, index) -> str:
    """Cite key for *paper*: first author's surname + year + citation index.

    The index is part of the key so that two papers by the same author in the
    same year cannot collide, and so the key and the ``[N]`` marker it replaces
    stay verifiably in step.
    """
    # Via _author_list, not the raw string: a record whose author field holds
    # the "Unknown Authors" placeholder was keyed `Authors2021`, i.e. cited as
    # though someone named Authors wrote it.
    authors = _author_list(paper)
    last_name = authors[0].split()[-1] if authors else "ref"
    last_name = re.sub(r"[^a-zA-Z]", "", last_name) or "ref"
    year = re.sub(r"[^0-9]", "", _clean(paper.get("year")))
    return f"{last_name}{year or index}{index}"


def _author_list(paper: dict) -> list:
    """Individual author names, with the ``et al.`` marker dropped."""
    raw = _clean(paper.get("authors"))
    if not raw:
        return []
    names = []
    for chunk in _AUTHOR_SPLIT_RE.split(raw):
        name = _TRAILING_ET_AL_RE.sub("", chunk.strip()).strip()
        if not name or _ET_AL_RE.match(name):
            continue
        # Several integrations emit this literal when a record has no author
        # list. Passed through, it becomes `AU  - Authors, Unknown` in RIS and a
        # person named "Unknown Authors" in Zotero.
        if name.lower() in ("unknown authors", "unknown author", "unknown", "n/a"):
            continue
        names.append(name)
    return names


def _split_surname(name: str) -> tuple:
    """``("Smith", "John A.")`` from ``"John A. Smith"``.

    Only ever a heuristic -- the sources hand us a display string, not
    structured name parts -- but RIS and CSL both require the split, and
    surname-last is right for the overwhelming majority of this corpus.
    """
    parts = name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _venue_field(entry: str) -> str:
    """Which BibTeX field carries the venue for this entry type."""
    if entry == "inproceedings":
        return "booktitle"
    if entry == "incollection":
        return "booktitle"
    if entry == "article":
        return "journal"
    return ""


def to_bibtex(references: list) -> str:
    """A .bib file for *references*, one entry each, in the given order."""
    entries = []
    for i, paper in enumerate(references or [], 1):
        if not isinstance(paper, dict):
            continue
        # Key off the persisted index so the .bib and the \cite{} markers agree
        # by construction rather than by both happening to enumerate the same way.
        index = str(paper.get("index") or i)
        key = bibtex_key(paper, index)
        entry = entry_type(paper)

        title = _bib_escape(_clean(paper.get("title")) or "Untitled")
        year = _clean(paper.get("year")) or "n.d."

        fields = [f"  title = {{{title}}}"]
        authors = _author_list(paper)
        if authors:
            fields.append(f"  author = {{{_bib_escape(' and '.join(authors))}}}")
        # Otherwise the field is omitted rather than filled with "Unknown".
        # Every style file has a rule for a missing author; none has one for an
        # author literally named Unknown, so the placeholder got typeset.
        fields.append(f"  year = {{{year}}}")

        venue = venue_of(paper)
        venue_field = _venue_field(entry)
        if venue and venue_field:
            fields.append(f"  {venue_field} = {{{_bib_escape(venue)}}}")
        elif venue:
            fields.append(f"  howpublished = {{{_bib_escape(venue)}}}")

        for field, source_key in (
            ("volume", "volume"),
            ("number", "issue"),
            ("pages", "pages"),
            ("publisher", "publisher"),
        ):
            value = _clean(paper.get(source_key))
            if value:
                fields.append(f"  {field} = {{{_bib_escape(value)}}}")

        if entry == "phdthesis" or entry == "techreport":
            institution = _clean(paper.get("publisher")) or _clean(paper.get("institution"))
            if institution and not any(f.startswith("  publisher") for f in fields):
                fields.append(f"  institution = {{{_bib_escape(institution)}}}")

        eprint = _clean(paper.get("eprint"))
        if eprint:
            fields.append(f"  eprint = {{{eprint}}}")
            fields.append("  archivePrefix = {arXiv}")

        doi = normalize_doi(paper.get("doi"))
        url = _clean(paper.get("url"))
        if doi:
            fields.append(f"  doi = {{{doi}}}")
        if url and not doi:
            # A URL is the fallback identifier, not a companion to the DOI:
            # printing both makes the entry twice as long and says the same
            # thing, since our URLs are overwhelmingly doi.org redirects.
            fields.append(f"  url = {{{url}}}")

        entries.append(f"@{entry}{{{key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries)


# BibTeX entry type -> RIS reference type.
_RIS_TYPES = {
    "article": "JOUR",
    "inproceedings": "CPAPER",
    "incollection": "CHAP",
    "book": "BOOK",
    "phdthesis": "THES",
    "techreport": "RPRT",
    "misc": "GEN",
}


def to_ris(references: list) -> str:
    """An RIS file for *references* — the format Zotero/Mendeley/EndNote import.

    RIS is line-oriented ``TAG  - value`` with a two-space separator that is
    part of the spec, not formatting. Every record ends with ``ER  -``.
    """
    lines = []
    for i, paper in enumerate(references or [], 1):
        if not isinstance(paper, dict):
            continue
        entry = entry_type(paper)
        lines.append(f"TY  - {_RIS_TYPES.get(entry, 'GEN')}")
        for surname, given in (_split_surname(n) for n in _author_list(paper)):
            lines.append(f"AU  - {surname}, {given}" if given else f"AU  - {surname}")
        title = _clean(paper.get("title"))
        if title:
            lines.append(f"TI  - {title}")
        year = _clean(paper.get("year"))
        if year.isdigit():
            lines.append(f"PY  - {year}")
        venue = venue_of(paper)
        if venue:
            # T2 is the container title for every RIS type we emit: journal for
            # JOUR, proceedings for CPAPER, book for CHAP.
            lines.append(f"T2  - {venue}")
        for tag, key in (("VL", "volume"), ("IS", "issue"), ("PB", "publisher")):
            value = _clean(paper.get(key))
            if value:
                lines.append(f"{tag}  - {value}")
        pages = _clean(paper.get("pages"))
        if pages:
            bounds = [p for p in re.split(r"-+", pages) if p.strip()]
            lines.append(f"SP  - {bounds[0].strip()}")
            if len(bounds) > 1:
                lines.append(f"EP  - {bounds[-1].strip()}")
        abstract = _clean(paper.get("abstract"))
        if abstract:
            lines.append(f"AB  - {abstract}")
        doi = normalize_doi(paper.get("doi"))
        if doi:
            lines.append(f"DO  - {doi}")
        url = _clean(paper.get("url"))
        if url:
            lines.append(f"UR  - {url}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


# BibTeX entry type -> CSL type.
_CSL_TYPES = {
    "article": "article-journal",
    "inproceedings": "paper-conference",
    "incollection": "chapter",
    "book": "book",
    "phdthesis": "thesis",
    "techreport": "report",
    "misc": "manuscript",
}


def to_csl_json(references: list) -> str:
    """CSL-JSON for *references* — what Pandoc and Zotero consume natively."""
    items = []
    for i, paper in enumerate(references or [], 1):
        if not isinstance(paper, dict):
            continue
        index = str(paper.get("index") or i)
        entry = entry_type(paper)
        item = {
            "id": bibtex_key(paper, index),
            "type": _CSL_TYPES.get(entry, "manuscript"),
            "title": _clean(paper.get("title")) or "Untitled",
        }
        authors = [
            {"family": surname, "given": given} if given else {"literal": surname}
            for surname, given in (_split_surname(n) for n in _author_list(paper))
        ]
        if authors:
            item["author"] = authors
        year = _clean(paper.get("year"))
        if year.isdigit():
            # CSL dates are nested arrays of date parts; a bare year is a
            # one-element part, not the integer.
            item["issued"] = {"date-parts": [[int(year)]]}
        venue = venue_of(paper)
        if venue:
            item["container-title"] = venue
        for field, key in (
            ("volume", "volume"),
            ("issue", "issue"),
            ("page", "pages"),
            ("publisher", "publisher"),
        ):
            value = _clean(paper.get(key))
            if value:
                item[field] = value.replace("--", "-") if field == "page" else value
        doi = normalize_doi(paper.get("doi"))
        if doi:
            item["DOI"] = doi
        url = _clean(paper.get("url"))
        if url:
            item["URL"] = url
        abstract = _clean(paper.get("abstract"))
        if abstract:
            item["abstract"] = abstract
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)
