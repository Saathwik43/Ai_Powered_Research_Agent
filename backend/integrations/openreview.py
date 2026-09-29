"""OpenReview API v2 — public notes search, no API key.

https://api2.openreview.net/notes/search returns forum heads (submissions and
indexed records) for a keyword query. Replies — reviews, comments, decisions —
are a different ``source`` and are not requested. Content fields on API v2 are
wrapped as ``{"value": ...}``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from integrations.http_client import pooled_client
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

OPENREVIEW_API = "https://api2.openreview.net"
OPENREVIEW_SEARCH_URL = f"{OPENREVIEW_API}/notes/search"
OPENREVIEW_WEB = "https://openreview.net"

_DOI_RE = re.compile(r"doi\.org/(10\.\S+)", re.I)
_BIBTEX_DOI_RE = re.compile(r"doi\s*=\s*\{([^}]+)\}", re.I)
_ARXIV_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arXiv\.)(?P<id>\d{4}\.\d{4,5})(?:v\d+)?",
    re.I,
)


def _content_value(content: dict, key: str):
    raw = content.get(key)
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value")
    return raw


def _authors(value) -> str:
    if isinstance(value, str):
        names = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        names = [str(n).strip() for n in value if str(n).strip()]
    else:
        names = []
    if not names:
        return ""
    shown = ", ".join(names[:3])
    return shown + (" et al." if len(names) > 3 else "")


def _year(note: dict) -> str:
    for key in ("pdate", "cdate"):
        raw = note.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            try:
                return str(datetime.fromtimestamp(raw / 1000, tz=timezone.utc).year)
            except (OverflowError, OSError, ValueError):
                continue
    return "Unknown"


def _doi(*candidates: str) -> str:
    for text in candidates:
        if not text:
            continue
        match = _DOI_RE.search(text)
        if match:
            return match.group(1).rstrip(".,)")
        bib = _BIBTEX_DOI_RE.search(text)
        if bib:
            return bib.group(1).strip()
    return ""


def _arxiv_id(*candidates: str) -> str:
    for text in candidates:
        if not text:
            continue
        match = _ARXIV_RE.search(text)
        if match:
            return match.group("id")
    return ""


def _pdf_url(note_id: str, pdf_value, invitations: list[str]) -> str:
    pdf = pdf_value.strip() if isinstance(pdf_value, str) else ""
    if pdf.startswith(("http://", "https://")):
        return pdf
    if pdf.startswith("/"):
        return OPENREVIEW_WEB + pdf
    joined = " ".join(invitations)
    if note_id and "Submission" in joined:
        return f"{OPENREVIEW_WEB}/pdf?id={note_id}"
    return ""


def _paper_type(venueid: str, invitations: list[str]) -> tuple[str, str]:
    """(type, subtype). Subtype is empty when it does not apply."""
    venueid_l = (venueid or "").lower()
    if "corr" in venueid_l:
        return "posted-content", "preprint"
    if "/journals/" in venueid_l:
        return "journal-article", ""
    if "/conf/" in venueid_l or any("Submission" in inv for inv in invitations):
        return "proceedings-article", ""
    return "proceedings-article", ""


def note_to_paper(note: dict) -> dict | None:
    """One forum note as a paper dict, or None when it is not a paper."""
    if not isinstance(note, dict):
        return None
    content = note.get("content") or {}
    if not isinstance(content, dict):
        return None

    title = _content_value(content, "title")
    if not isinstance(title, str) or not title.strip():
        return None

    note_id = str(note.get("id") or "").strip()
    invitations = [str(i) for i in (note.get("invitations") or []) if i]
    venue = _content_value(content, "venue") or ""
    venueid = _content_value(content, "venueid") or ""
    if not isinstance(venue, str):
        venue = ""
    if not isinstance(venueid, str):
        venueid = ""

    abstract = _content_value(content, "abstract") or ""
    if not isinstance(abstract, str):
        abstract = ""

    html = _content_value(content, "html") or ""
    if not isinstance(html, str):
        html = ""
    pdf_value = _content_value(content, "pdf")
    bibtex = _content_value(content, "_bibtex") or ""
    if not isinstance(bibtex, str):
        bibtex = ""

    pdf_url = _pdf_url(note_id, pdf_value, invitations)
    doi = _doi(html, bibtex)
    arxiv_id = _arxiv_id(pdf_url, html, doi, bibtex)
    paper_type, subtype = _paper_type(venueid, invitations)

    if html.startswith(("http://", "https://")):
        url = html
    elif note_id:
        url = f"{OPENREVIEW_WEB}/forum?id={note_id}"
    else:
        url = ""

    keywords = _content_value(content, "keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    categories = [str(k).strip() for k in keywords if str(k).strip()] if isinstance(keywords, list) else []

    paper = {
        "id": note_id or url,
        "title": title.strip(),
        "authors": _authors(_content_value(content, "authors")),
        "year": _year(note),
        "abstract": abstract.strip(),
        "url": url,
        "pdf_url": pdf_url,
        "doi": doi,
        "citations": 0,
        "venue": venue.strip(),
        "categories": categories,
        "source": "OpenReview",
        "type": paper_type,
    }
    if subtype:
        paper["subtype"] = subtype
    if arxiv_id:
        paper["eprint"] = arxiv_id
    return paper


async def search_papers(query: str, limit: int = 15) -> list:
    """Search OpenReview forum notes (papers, not reviews)."""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 15), 100))
    params = {
        "term": query,
        "source": "forum",
        "content": "all",
        "limit": limit,
    }

    async with track_call("OpenReview", "search") as rec:
        try:
            async with pooled_client(timeout=15.0) as client:
                resp = await client.get(OPENREVIEW_SEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            notes = data.get("notes") or []
            papers = []
            for note in notes:
                paper = note_to_paper(note)
                if paper:
                    papers.append(paper)
            rec.succeed(http_status=resp.status_code, items=len(papers))
            return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"OpenReview search error: {e}")
            return []
