import os
import httpx
from core.retry import is_retryable, with_retry
from integrations.http_client import pooled_client
import logging
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
_FIELDS = (
    "title,authors,year,citationCount,abstract,url,openAccessPdf,externalIds,"
    # For the bibliography (2.8): venue names the journal or proceedings,
    # publicationTypes distinguishes JournalArticle from Conference, and
    # `journal` carries volume/pages that nothing else here reports.
    "venue,publicationTypes,journal,publicationVenue"
)

logger = logging.getLogger(__name__)


def _normalize_item(item: dict) -> dict:
    authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    if not author_str:
        author_str = "Unknown Authors"

    pdf_url = ""
    oa_pdf = item.get("openAccessPdf")
    if oa_pdf and isinstance(oa_pdf, dict):
        pdf_url = oa_pdf.get("url", "")

    ext = item.get("externalIds") or {}
    doi = ext.get("DOI") or ""

    normalized = {
        "id": item.get("paperId", ""),
        "title": item.get("title", "Untitled"),
        "authors": author_str,
        "year": str(item.get("year", "Unknown")),
        "citations": item.get("citationCount", 0),
        "abstract": item.get("abstract") or "No abstract available.",
        "url": item.get("url", ""),
        "pdf_url": pdf_url,
        "doi": doi,
        "source": "Semantic Scholar",
    }
    normalized.update(_bibliographic_fields(item, ext))
    return normalized


# S2 publicationTypes -> the Crossref vocabulary the entry-type mapper speaks,
# so every source hands the bibliography one type language instead of three.
_S2_TYPE_MAP = {
    "JournalArticle": "journal-article",
    "Conference": "proceedings-article",
    "Book": "book",
    "BookSection": "book-chapter",
    "Thesis": "dissertation",
    "Report": "report",
    "Dataset": "dataset",
    "Review": "journal-article",
}


def _bibliographic_fields(item: dict, ext: dict) -> dict:
    """Venue, entry type and page numbers for the bibliography (2.8).

    Keys with no real value are omitted rather than set empty — the dedupe
    merge fills gaps across records, so a blank here would mask a better
    record's venue for the same paper.
    """
    out = {}

    venue = (item.get("venue") or "").strip()
    publication_venue = item.get("publicationVenue") or {}
    if not venue:
        venue = (publication_venue.get("name") or "").strip()
    if venue:
        out["venue"] = venue
    venue_type = (publication_venue.get("type") or "").strip()
    if venue_type:
        out["venue_type"] = venue_type

    for raw in item.get("publicationTypes") or []:
        mapped = _S2_TYPE_MAP.get(raw)
        if mapped:
            out["type"] = mapped
            break

    journal = item.get("journal") or {}
    volume = str(journal.get("volume") or "").strip()
    if volume:
        out["volume"] = volume
    pages = str(journal.get("pages") or "").strip()
    if pages:
        out["pages"] = pages
    # S2 reports the journal name here too on records where `venue` is blank.
    if not out.get("venue"):
        journal_name = (journal.get("name") or "").strip()
        if journal_name:
            out["venue"] = journal_name

    arxiv_id = (ext.get("ArXiv") or "").strip()
    if arxiv_id:
        out["eprint"] = arxiv_id
        out.setdefault("type", "posted-content")

    return out


async def search_papers(query: str, limit: int = 8) -> list:
    """
    Search Semantic Scholar by keyword.
    Retries 429s, then falls back to the bulk endpoint (separate rate pool).
    """
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    headers = {"x-api-key": api_key} if api_key else {}
    params = {"query": query, "limit": limit, "fields": _FIELDS}

    async with track_call("Semantic Scholar", "search") as rec:
        try:
            async with pooled_client(timeout=15.0) as client:
                last_status = None

                async def attempt_search():
                    resp = await client.get(S2_SEARCH_URL, params=params, headers=headers)
                    resp.raise_for_status()
                    return resp

                try:
                    # One retry policy (`core.retry`): honours `Retry-After`,
                    # jitters otherwise, and stops before the caller's own
                    # deadline. This loop used to clamp Retry-After to 4s and
                    # sleep a fixed 1.5s per attempt otherwise — so several
                    # concurrent searches all retried in the same instant.
                    resp = await with_retry(
                        attempt_search, name="Semantic Scholar", attempts=3, budget=6.0
                    )
                except Exception as exc:
                    last_status = getattr(getattr(exc, "response", None), "status_code", None)
                    if not is_retryable(exc):
                        raise
                else:
                    papers = [_normalize_item(item) for item in resp.json().get("data", [])]
                    rec.succeed(http_status=resp.status_code, items=len(papers))
                    return papers

                bulk = await client.get(
                    S2_BULK_URL,
                    params={"query": query, "fields": _FIELDS},
                    headers=headers,
                )
                last_status = bulk.status_code
                if bulk.status_code == 200:
                    papers = [_normalize_item(item) for item in bulk.json().get("data", [])][:limit]
                    rec.succeed(http_status=200, items=len(papers))
                    logger.info("Semantic Scholar search 429'd; bulk fallback returned %s papers", len(papers))
                    return papers

                rec.fail(http_status=last_status, error="Rate limited")
                logger.error("Semantic Scholar Error: HTTP %s after retries + bulk fallback", last_status)
                return []
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"Semantic Scholar Error: {e}")
            return []
