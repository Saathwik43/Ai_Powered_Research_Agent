"""ACL Anthology bibliography.

The Anthology does not offer a per-query search API. Its official machine-readable
export is the full BibTeX bibliography at ``anthology.bib.gz`` (about 13 MB).
That file is downloaded once, parsed off the event loop, and kept in memory for
a day. A search then matches title tokens against the cached index.

Proceedings volumes (the ``@proceedings`` entries) are skipped. Papers keep the
venue, DOI, pages, and a PDF link so a later dedupe can fill those in on a
copy that arrived from another source with a fuller abstract.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
import time

from integrations.http_client import pooled_client
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

BIB_URL = "https://aclanthology.org/anthology.bib.gz"
_INDEX_TTL = 24 * 60 * 60

_KEEP = {
    "inproceedings": "proceedings-article",
    "article": "journal-article",
    "incollection": "book-chapter",
    "book": "book",
    "phdthesis": "dissertation",
    "mastersthesis": "dissertation",
}

_ENTRY_RE = re.compile(r"@(?P<type>[A-Za-z]+)\{(?P<key>[^,\s]+)\s*,", re.S)
_FIELD_RE = re.compile(r"([A-Za-z]+)\s*=\s*\"((?:[^\"\\]|\\.)*)\"", re.S)
_BRACE_RE = re.compile(r"[{}]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ANTHOLOGY_ID_RE = re.compile(
    r"\b(?:\d{4}\.[a-z0-9]+(?:-[a-z0-9]+)*\.\d+|[a-z]\d{2}-\d{4})\b",
    re.I,
)
_URL_ID_RE = re.compile(r"aclanthology\.org/([^/\s\"#]+)", re.I)
_AND_RE = re.compile(r"\s+and\s+", re.I)

_STOP = {
    "and", "the", "of", "in", "for", "a", "an", "to", "on", "with", "is", "by",
    "from", "based", "using", "via", "into", "over", "its", "their",
}

# (records, token -> record indexes, anthology id -> record index)
_index: tuple[list[dict], dict[str, list[int]], dict[str, int]] | None = None
_loaded_at = 0.0
_load_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def _clean_text(value: str) -> str:
    return " ".join(_BRACE_RE.sub("", value or "").split())


def _authors(raw: str) -> str:
    names = []
    for part in _AND_RE.split((raw or "").replace("\n", " ")):
        part = " ".join(part.split())
        if not part:
            continue
        if "," in part:
            last, first = part.split(",", 1)
            names.append(f"{first.strip()} {last.strip()}".strip())
        else:
            names.append(part)
    if not names:
        return ""
    shown = ", ".join(names[:3])
    return shown + (" et al." if len(names) > 3 else "")


def _anthology_id(url: str) -> str:
    match = _URL_ID_RE.search(url or "")
    if not match:
        return ""
    return match.group(1).removesuffix(".pdf").strip().lower()


def _pdf_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith(".pdf"):
        return cleaned
    return cleaned + ".pdf"


def parse_bibliography(text: str) -> list[dict]:
    """BibTeX text to paper dicts. Volumes without an author are dropped."""
    matches = list(_ENTRY_RE.finditer(text or ""))
    papers = []
    for i, match in enumerate(matches):
        entry_type = match.group("type").lower()
        mapped = _KEEP.get(entry_type)
        if not mapped:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields = {
            name.lower(): _clean_text(value)
            for name, value in _FIELD_RE.findall(text[match.end():end])
        }
        title = fields.get("title") or ""
        if not title:
            continue
        authors = _authors(fields.get("author") or "")
        if not authors:
            continue
        url = (fields.get("url") or "").strip()
        venue = fields.get("journal") or fields.get("booktitle") or ""
        paper = {
            "id": _anthology_id(url) or match.group("key"),
            "title": title,
            "authors": authors,
            "year": (fields.get("year") or "Unknown").strip() or "Unknown",
            "abstract": "",
            "url": url,
            "pdf_url": _pdf_url(url),
            "doi": (fields.get("doi") or "").strip(),
            "citations": 0,
            "venue": venue,
            "publisher": (fields.get("publisher") or "").strip(),
            "source": "ACLAnthology",
            "type": mapped,
        }
        pages = (fields.get("pages") or "").strip()
        if pages:
            paper["pages"] = pages
        if not paper["publisher"]:
            paper.pop("publisher")
        papers.append(paper)
    return papers


def _tokens(text: str) -> list[str]:
    return [
        token for token in _TOKEN_RE.findall((text or "").lower())
        if token not in _STOP and len(token) > 2
    ]


def build_index(text: str):
    """Parse *text* and build the token and id indexes used by search."""
    records = parse_bibliography(text)
    postings: dict[str, list[int]] = {}
    by_id: dict[str, int] = {}
    for i, paper in enumerate(records):
        anthology_id = str(paper.get("id") or "").lower()
        if anthology_id:
            by_id[anthology_id] = i
        for token in set(_tokens(paper.get("title") or "")):
            postings.setdefault(token, []).append(i)
    return records, postings, by_id


def search_index(index, query: str, limit: int) -> list[dict]:
    """Top *limit* papers whose titles share tokens with *query*."""
    records, postings, by_id = index
    limit = max(1, min(int(limit or 15), 100))
    chosen: list[int] = []
    seen: set[int] = set()

    for anthology_id in _ANTHOLOGY_ID_RE.findall(query or ""):
        idx = by_id.get(anthology_id.lower())
        if idx is not None and idx not in seen:
            chosen.append(idx)
            seen.add(idx)

    tokens = _tokens(query)
    scores: dict[int, int] = {}
    for token in set(tokens):
        for idx in postings.get(token, ()):
            scores[idx] = scores.get(idx, 0) + 1

    ranked = sorted(
        (idx for idx in scores if idx not in seen),
        key=lambda idx: (scores[idx], _year(records[idx])),
        reverse=True,
    )
    chosen.extend(ranked[: max(0, limit - len(chosen))])
    return [dict(records[idx]) for idx in chosen[:limit]]


def _year(paper: dict) -> int:
    try:
        return int(str(paper.get("year") or "")[:4])
    except ValueError:
        return 0


def _note_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("ACL Anthology index load failed: %s", exc)


async def _download_bibliography() -> str:
    async with pooled_client(timeout=25.0) as client:
        resp = await client.get(BIB_URL)
        resp.raise_for_status()
        raw = resp.content
    return gzip.decompress(raw).decode("utf-8")


async def _build_index():
    global _index, _loaded_at
    text = await _download_bibliography()
    built = await asyncio.to_thread(build_index, text)
    _index = built
    _loaded_at = time.time()
    logger.info("ACL Anthology index ready: %d papers", len(built[0]))
    return built


async def _cached_index():
    """The in-memory index, loading it once per day.

    The load runs as its own task and is shielded, so a fan-out that gives up
    on this source mid-download does not throw the bytes away. The next search
    waits for the same task instead of starting another 13 MB fetch.
    """
    global _load_task
    now = time.time()
    if _index is not None and (now - _loaded_at) < _INDEX_TTL:
        return _index
    async with _lock:
        now = time.time()
        if _index is not None and (now - _loaded_at) < _INDEX_TTL:
            return _index
        if _load_task is None or _load_task.done():
            _load_task = asyncio.create_task(_build_index())
            _load_task.add_done_callback(_note_failure)
        task = _load_task
    return await asyncio.shield(task)


async def warm_index() -> None:
    """Download the bibliography ahead of the first search. Failures are logged."""
    try:
        await _cached_index()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("ACL Anthology index was not warmed", exc_info=True)


async def close_index() -> None:
    """Cancel an in-flight bibliography download on shutdown."""
    global _load_task
    task = _load_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def search_papers(query: str, limit: int = 15) -> list:
    """Search the cached ACL Anthology bibliography by title."""
    query = (query or "").strip()
    if not query:
        return []

    async with track_call("ACLAnthology", "search") as rec:
        try:
            index = await _cached_index()
            papers = search_index(index, query, limit)
            rec.succeed(http_status=200, items=len(papers))
            return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"ACL Anthology search error: {e}")
            return []
