import os
import re
import httpx
from core.paper_identity import normalize_arxiv_id, paper_doi
from core.retry import is_retryable, with_retry
from integrations.http_client import pooled_client
import logging
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
S2_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1/paper"
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


# ─── Citation traversal (2.2) ─────────────────────────────────────────────────

# S2's own paper ids are 40 hex characters. Anything else has to be addressed
# through one of the external-id prefixes S2 accepts.
_S2_PAPER_ID_RE = re.compile(r"^[0-9a-f]{40}$")

# arXiv's DataCite DOI, as `core.paper_identity.normalize_doi` lowercases it.
_ARXIV_DOI_PREFIX = "10.48550/arxiv."

# ``/references`` returns the works the paper cites (backward) and
# ``/citations`` the works citing it (forward). The nested record is under a
# different key in each response, so direction picks both at once.
_DIRECTION_ENDPOINT = {
    "backward": ("references", "citedPaper"),
    "forward": ("citations", "citingPaper"),
}

# `isInfluential` is an edge field, not a paper field: S2's own judgement that
# the citing paper actually builds on the cited one rather than name-checking
# it. It is the cheapest quality signal in the whole snowball, so it is asked
# for and carried through to ranking.
_EDGE_FIELDS = f"{_FIELDS},isInfluential"


def _record_empty(rec, http_status: int) -> None:
    """Record a call that worked and found nothing, without calling it a failure.

    ``rec.succeed(items=0)`` is deliberately counted as *not ok* by the
    telemetry helper, because a keyword search returning zero papers usually
    means the library is broken. The opposite is true here: plenty of real
    papers have no resolved references in S2, and a snowball issues one of these
    requests per seed per direction. Counting those as failures would feed the
    circuit breaker until it skipped Semantic Scholar — for keyword search too.
    """
    rec.succeed(http_status=http_status)


def paper_ref(paper: dict) -> str:
    """How to address *paper* in the S2 graph API, or ''.

    No request: S2 accepts a DOI or arXiv id directly, so a paper that came
    from any source with either identifier is reachable without a lookup.

    A publisher DOI is preferred — it names the published version, whose
    reference list S2 has from the publisher rather than from a PDF parse. The
    exception is arXiv's own DataCite DOI, which S2 does **not** index: asking
    for ``DOI:10.48550/arxiv.1706.03762`` returns nothing for a paper that
    answers perfectly under ``ARXIV:1706.03762``. Verified against the live API,
    2026-09-25. Any source that hands us an arXiv record gives us both forms, so
    preferring the DOI silently lost every arXiv-only seed.
    """
    identifier = paper.get("id")
    if isinstance(identifier, str) and _S2_PAPER_ID_RE.match(identifier.strip().lower()):
        return identifier.strip().lower()

    doi = paper_doi(paper)
    arxiv_id = normalize_arxiv_id(paper)

    if doi and not (arxiv_id and doi.startswith(_ARXIV_DOI_PREFIX)):
        return f"DOI:{doi}"
    if arxiv_id:
        return f"ARXIV:{arxiv_id}"
    if doi:
        # An arXiv DataCite DOI with no parseable id behind it. Worth asking.
        return f"DOI:{doi}"

    return ""


async def fetch_related(paper: dict, direction: str, limit: int = 25) -> list[dict]:
    """Works cited by *paper* (``backward``) or citing it (``forward``).

    Returns [] for a paper S2 cannot address or does not know, which is a
    normal outcome for a snowball and must not be recorded as a source failure
    — doing so would open the circuit breaker on a healthy API just because the
    seed set happened to contain papers without identifiers.
    """
    endpoint = _DIRECTION_ENDPOINT.get(direction)
    if endpoint is None:
        raise ValueError(f"direction must be one of {sorted(_DIRECTION_ENDPOINT)}, got {direction!r}")
    path, nested_key = endpoint

    ref = paper_ref(paper)
    if not ref:
        return []

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    headers = {"x-api-key": api_key} if api_key else {}
    # S2 caps `limit` at 1000 on these endpoints; the caller's per-seed budget
    # is far below that, so the cap is only here to keep a bad argument honest.
    params = {"fields": _EDGE_FIELDS, "limit": max(1, min(limit, 1000))}

    async with track_call("Semantic Scholar", f"snowball-{direction}") as rec:
        try:
            async with pooled_client(timeout=15.0) as client:

                async def attempt():
                    resp = await client.get(
                        f"{S2_GRAPH_BASE}/{ref}/{path}", params=params, headers=headers
                    )
                    resp.raise_for_status()
                    return resp

                try:
                    response = await with_retry(
                        attempt, name="Semantic Scholar", attempts=3, budget=6.0
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        # S2 has no record under this identifier. A real answer
                        # about the paper, not a broken API — see _record_empty.
                        _record_empty(rec, 404)
                        return []
                    raise

                papers = []
                for edge in response.json().get("data", []):
                    nested = edge.get(nested_key)
                    if not isinstance(nested, dict) or not nested.get("title"):
                        # S2 returns a null nested paper for a reference it
                        # parsed out of the PDF but never resolved to a record.
                        continue
                    normalized = _normalize_item(nested)
                    if edge.get("isInfluential"):
                        normalized["influential"] = True
                    papers.append(normalized)

                if papers:
                    rec.succeed(http_status=response.status_code, items=len(papers))
                else:
                    _record_empty(rec, response.status_code)
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error("Semantic Scholar %s citation fetch failed: %s", direction, e)
            # Raised, not swallowed into []. This is the one caller
            # (`integrations/snowball.py`), it catches per request, and it has
            # to distinguish "this graph knows no edges for your seeds" from
            # "this graph is down" — returning [] makes an outage look like an
            # answer in the source panel. `search_papers` above still returns []
            # because the fan-out there treats an empty list as its own signal.
            raise
