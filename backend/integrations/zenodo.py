"""Zenodo records API — public publication search, no API key.

``GET https://zenodo.org/api/records`` returns the current versions of deposits.
Datasets and software are dropped; only ``resource_type.type == publication``
is kept. Open files become the PDF link. The description is HTML, so tags are
stripped before it is stored as the abstract.
"""

from __future__ import annotations

import html
import logging
import re

from integrations.http_client import pooled_client
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

ZENODO_SEARCH_URL = "https://zenodo.org/api/records"

_BLOCK_RE = re.compile(r"</?(?:p|div|br|li|h\d|tr|blockquote)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

# Zenodo publication subtype -> (paper type, subtype or "").
_TYPE_MAP = {
    "conferencepaper": ("proceedings-article", ""),
    "article": ("journal-article", ""),
    "preprint": ("posted-content", "preprint"),
    "workingpaper": ("posted-content", "preprint"),
    "thesis": ("dissertation", ""),
    "report": ("report", ""),
    "deliverable": ("report", ""),
    "book": ("book", ""),
    "section": ("book-chapter", ""),
    "other": ("other", ""),
}


def _plain(value: str) -> str:
    text = _BLOCK_RE.sub(" ", value or "")
    text = _TAG_RE.sub("", text)
    return " ".join(html.unescape(text).split())


def _person(name: str) -> str:
    name = " ".join((name or "").split())
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}".strip()


def _authors(creators) -> str:
    names = []
    for creator in creators or []:
        if not isinstance(creator, dict):
            continue
        person = _person(creator.get("name") or "")
        if person:
            names.append(person)
    if not names:
        return ""
    shown = ", ".join(names[:3])
    return shown + (" et al." if len(names) > 3 else "")


def _is_current_version(metadata: dict) -> bool:
    versions = ((metadata.get("relations") or {}).get("version") or [])
    if not isinstance(versions, list) or not versions:
        return True
    marked = [v for v in versions if isinstance(v, dict) and "is_last" in v]
    if not marked:
        return True
    return any(v.get("is_last") is True for v in marked)


def _venue(metadata: dict) -> str:
    meeting = metadata.get("meeting") or {}
    if isinstance(meeting, dict):
        title = _plain(meeting.get("title") or meeting.get("acronym") or "")
        if title:
            return title
    imprint = metadata.get("imprint") or {}
    if isinstance(imprint, dict):
        return _plain(imprint.get("title") or imprint.get("publisher") or "")
    return ""


def _pdf_url(hit: dict, access: str) -> str:
    if (access or "").lower() != "open":
        return ""
    for file in hit.get("files") or []:
        if not isinstance(file, dict):
            continue
        key = str(file.get("key") or "")
        link = ((file.get("links") or {}).get("self") or "").strip()
        if link and key.lower().endswith(".pdf"):
            return link
    return ""


def record_to_paper(hit: dict) -> dict | None:
    """One Zenodo record as a paper dict, or None when it is not a publication."""
    if not isinstance(hit, dict):
        return None
    metadata = hit.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    resource = metadata.get("resource_type") or {}
    if not isinstance(resource, dict):
        resource = {}
    kind = str(resource.get("type") or "").lower()
    if kind and kind != "publication":
        return None
    if not _is_current_version(metadata):
        return None

    title = _plain(metadata.get("title") or "")
    if not title:
        return None

    subtype_name = str(resource.get("subtype") or "").lower()
    paper_type, subtype = _TYPE_MAP.get(subtype_name, ("other", ""))
    published = str(metadata.get("publication_date") or "")
    year = published[:4] if len(published) >= 4 and published[:4].isdigit() else "Unknown"
    doi = str(metadata.get("doi") or hit.get("doi") or "").strip()
    links = hit.get("links") or {}
    url = ""
    if isinstance(links, dict):
        url = (links.get("self_html") or links.get("doi") or "").strip()
    if not url and doi:
        url = f"https://doi.org/{doi}"

    keywords = metadata.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    categories = [_plain(k) for k in keywords if _plain(str(k))] if isinstance(keywords, list) else []

    paper = {
        "id": str(hit.get("id") or doi or url),
        "title": title,
        "authors": _authors(metadata.get("creators")),
        "year": year,
        "abstract": _plain(metadata.get("description") or ""),
        "url": url,
        "pdf_url": _pdf_url(hit, str(metadata.get("access_right") or "")),
        "doi": doi,
        "citations": 0,
        "venue": _venue(metadata),
        "categories": categories,
        "source": "Zenodo",
        "type": paper_type,
    }
    if subtype:
        paper["subtype"] = subtype
    if not paper["venue"]:
        paper.pop("venue")
    return paper


async def search_papers(query: str, limit: int = 15) -> list:
    """Search Zenodo for publications."""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 15), 100))
    params = {
        "q": query,
        "size": limit,
        "type": "publication",
        "sort": "bestmatch",
    }

    async with track_call("Zenodo", "search") as rec:
        try:
            async with pooled_client(timeout=12.0) as client:
                resp = await client.get(ZENODO_SEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            hits = ((data.get("hits") or {}).get("hits")) or []
            papers = []
            seen_concepts: set[str] = set()
            for hit in hits:
                concept = str((hit or {}).get("conceptrecid") or (hit or {}).get("id") or "")
                if concept and concept in seen_concepts:
                    continue
                paper = record_to_paper(hit)
                if not paper:
                    continue
                if concept:
                    seen_concepts.add(concept)
                papers.append(paper)
            rec.succeed(http_status=resp.status_code, items=len(papers))
            return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"Zenodo search error: {e}")
            return []
