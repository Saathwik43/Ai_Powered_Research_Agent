"""
Paper identity — one definition of "these two records are the same paper".

Lives in ``core`` because two layers need it and neither should depend on the
other: ``integrations/paper_search.py`` deduplicates fan-out results with it,
and ``ai/relevance.py`` keys its classification-verdict cache on it. When the
two disagreed, the relevance cache keyed on a 60-character title prefix and two
genuinely different papers sharing that prefix shared one yes/no verdict.

Identity is DOI → arXiv id → normalized title, in that order of authority.
"""

import hashlib
import re

__all__ = [
    "normalize_title",
    "normalize_doi",
    "paper_doi",
    "normalize_arxiv_id",
    "identity_keys",
    "paper_identity",
]


def normalize_title(title: str) -> str:
    """
    Lowercase, fold every punctuation run to a single space, collapse
    whitespace.

    Folding to a space rather than deleting matters: deleting turned
    "deep-learning" into "deeplearning" while "deep — learning" became
    "deep  learning" (two spaces), so the same title from two sources produced
    two different keys and both copies survived dedup.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split())


def normalize_doi(doi: str) -> str:
    """Lowercase and strip URL prefixes. Returns '' unless the value is a real DOI."""
    if not doi or not isinstance(doi, str):
        return ""
    cleaned = (
        doi.replace("https://doi.org/", "")
        .replace("http://doi.org/", "")
        .replace("https://dx.doi.org/", "")
        .replace("http://dx.doi.org/", "")
        .replace("doi:", "")
        .strip()
        .lower()
    )
    if not cleaned.startswith("10."):
        return ""
    return cleaned


def paper_doi(paper: dict) -> str:
    """Normalized DOI from the doi field, the id field, or a doi.org URL."""
    for candidate in (paper.get("doi"), paper.get("id")):
        normalized = normalize_doi(candidate or "")
        if normalized:
            return normalized
    url = paper.get("url") or ""
    return normalize_doi(url) if "doi.org/" in url else ""


# arXiv identity appears as an abs/pdf URL, a bare "arXiv:2301.00001" string,
# or a DataCite DOI ("10.48550/arXiv.2301.00001"). All three name one paper.
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(?P<id>[^\s?#]+)", re.I)
_ARXIV_TOKEN_RE = re.compile(
    r"arxiv[:.\-/\s]\s*(?P<id>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})", re.I
)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.I)


def normalize_arxiv_id(paper: dict) -> str:
    """arXiv identifier for *paper*, version suffix stripped, or ''."""
    for field in ("arxiv_id", "id", "url", "pdf_url", "doi"):
        value = paper.get(field)
        if not isinstance(value, str) or not value:
            continue
        for pattern in (_ARXIV_URL_RE, _ARXIV_TOKEN_RE):
            match = pattern.search(value)
            if not match:
                continue
            arxiv_id = match.group("id").strip().lower().removesuffix(".pdf")
            arxiv_id = _ARXIV_VERSION_RE.sub("", arxiv_id)
            if arxiv_id:
                return arxiv_id
    return ""


def identity_keys(paper: dict) -> list[str]:
    """
    Every identifier under which *paper* should be recognised as already seen.

    A paper matches an existing one if they share **any** key, which is what
    merges an arXiv preprint with its published version: the preprint carries
    the arXiv id, the published record carries the DOI, and both carry the
    title.
    """
    keys = []

    doi = paper_doi(paper)
    if doi:
        keys.append(f"doi:{doi}")

    arxiv_id = normalize_arxiv_id(paper)
    if arxiv_id:
        keys.append(f"arxiv:{arxiv_id}")

    title = normalize_title(paper.get("title") or "")
    if title:
        # Full title, never a 60-char prefix — truncation collapsed
        # "…Segmentation Part I" and "…Part II" into one paper. Short titles
        # ("Editorial", "Corrigendum") are too generic to stand alone, so they
        # are qualified by year.
        if len(title) >= 10:
            keys.append(f"title:{title}")
        else:
            year = str(paper.get("year") or "").strip()
            keys.append(f"title:{title}|{year}")

    return keys


def paper_identity(paper: dict) -> str:
    """
    One stable identity string for *paper*, for use as a cache key.

    The strongest available identifier wins, so two records of the same paper
    that both carry a DOI agree. Two records of the same paper that carry
    *different* kinds of identifier (a DOI on one, only a title on the other)
    get separate keys — a redundant classification call, never a shared verdict
    between two different papers, which is the failure this replaces.

    Papers with no identifier at all fall back to a content digest rather than
    collapsing onto a shared empty key.
    """
    keys = identity_keys(paper)
    if keys:
        return keys[0]
    blob = f"{paper.get('title') or ''}|{(paper.get('abstract') or '')[:500]}"
    return "blob:" + hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()
