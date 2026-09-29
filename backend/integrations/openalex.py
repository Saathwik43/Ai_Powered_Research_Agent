import os
import re
import httpx
from core.paper_identity import paper_doi
from core.retry import with_retry
from integrations.http_client import pooled_client
import logging
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

logger = logging.getLogger(__name__)

WORKS_URL = "https://api.openalex.org/works"

# Every field the normalizer reads. Shared by search and by the citation
# traversal (2.2) so a snowballed record and a searched one are the same shape
# — the dedupe merge in paper_search folds them together and a field missing
# from one path would look like a paper that genuinely lacks it.
_SELECT = (
    "id,doi,title,publication_year,cited_by_count,authorships,"
    "abstract_inverted_index,type,type_crossref,primary_location,biblio"
)

# "https://openalex.org/W2741809807" and a bare "W2741809807" both name a work.
_WORK_ID_RE = re.compile(r"\bW\d{4,}\b")


def _bibliographic_fields(item: dict) -> dict:
    """Venue, entry type and page numbers for the bibliography (2.8).

    Only keys with real values are returned. Absent keys matter: the dedupe
    merge in ``paper_search._merge_into`` fills a gap in one record from
    another, so emitting ``"venue": ""`` here would let a bare OpenAlex hit
    mask the venue a Crossref record for the same paper actually knows.
    """
    out = {}

    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    venue = (source.get("display_name") or "").strip()
    if venue:
        out["venue"] = venue
    # "journal" | "conference" | "repository" | "book series" ... The entry-type
    # mapper prefers this over guessing from the venue's name.
    host_type = (source.get("type") or "").strip()
    if host_type:
        out["venue_type"] = host_type
    publisher = (source.get("host_organization_name") or "").strip()
    if publisher:
        out["publisher"] = publisher

    # type_crossref separates journal-article from proceedings-article; plain
    # `type` calls both "article". Prefer the former, keep the latter as the
    # fallback for records Crossref never indexed (preprints, datasets).
    work_type = (item.get("type_crossref") or item.get("type") or "").strip()
    if work_type:
        out["type"] = work_type

    biblio = item.get("biblio") or {}
    volume = (biblio.get("volume") or "").strip()
    if volume:
        out["volume"] = volume
    issue = (biblio.get("issue") or "").strip()
    if issue:
        out["issue"] = issue
    first, last = (biblio.get("first_page") or "").strip(), (biblio.get("last_page") or "").strip()
    if first:
        out["pages"] = f"{first}--{last}" if last and last != first else first

    return out


def _normalize_work(item: dict) -> dict:
    """One OpenAlex work as the paper dict the rest of the app speaks.

    Shared by keyword search and citation traversal — see `_SELECT`.
    """
    authorships = item.get("authorships", [])
    authors = [a.get("author", {}).get("display_name", "") for a in authorships if a.get("author")]
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    if not author_str:
        author_str = "Unknown Authors"

    abstract_inverted = item.get("abstract_inverted_index")
    abstract = "No abstract available"
    if abstract_inverted:
        word_index = []
        for word, positions in abstract_inverted.items():
            for pos in positions:
                word_index.append((pos, word))
        word_index.sort(key=lambda x: x[0])
        abstract = " ".join([word for pos, word in word_index])

    doi_url = item.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "") if doi_url else ""
    paper = {
        "id": item.get("id"),
        "title": item.get("title", "Untitled"),
        "authors": author_str,
        "year": item.get("publication_year", "Unknown"),
        "citations": item.get("cited_by_count", 0),
        "abstract": abstract,
        "url": doi_url or item.get("id"),
        "doi": doi,
        "source": "OpenAlex",
    }
    paper.update(_bibliographic_fields(item))
    return paper


def _request_context() -> tuple[dict, dict]:
    """Polite-pool headers and the auth/mailto params every call shares."""
    email = os.getenv("CROSSREF_MAILTO", "")
    headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{email})" if email else "ResearchAgent/1.0"}
    params: dict = {}
    if email:
        params["mailto"] = email
    api_key = os.getenv("OPENALEX_API_KEY", "")
    if api_key:
        params["api_key"] = api_key
    return headers, params


async def search_papers(query: str, limit: int = 5):
    """
    Searches the OpenAlex API for research papers matching the query.
    Extracts title, authors, year, citation count, and abstract.
    """
    headers, params = _request_context()
    params.update({
        "search": query,
        "per-page": limit,
        # `type_crossref` is selected alongside `type` on purpose: OpenAlex's own
        # `type` reports "article" for a conference paper as well as a journal
        # one, so it cannot tell @article from @inproceedings. The Crossref
        # vocabulary ("journal-article" / "proceedings-article") can, and 2.8
        # needs that distinction to emit a correct entry type.
        "select": _SELECT,
    })

    async with track_call("OpenAlex", "search") as rec:
        try:
            async with pooled_client(headers=headers) as client:
                response = await client.get(WORKS_URL, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()

                papers = [_normalize_work(item) for item in data.get("results", [])]
                rec.succeed(http_status=response.status_code, items=len(papers))
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"OpenAlex Error: {e}")
            return []


# ─── Citation traversal (2.2) ─────────────────────────────────────────────────

# Both directions are the same request with a different filter, so they share
# one code path rather than drifting into two:
#   cited_by:W1  → the works W1 cites   (backward — its reference list)
#   cites:W1     → the works citing W1  (forward  — who built on it)
_DIRECTION_FILTER = {"backward": "cited_by", "forward": "cites"}


def _record_empty(rec, http_status: int) -> None:
    """Record a call that worked and found nothing, without calling it a failure.

    ``rec.succeed(items=0)`` is deliberately counted as *not ok* by the
    telemetry helper, because a keyword search returning zero papers usually
    means the library is broken. The opposite is true here: plenty of real
    papers have no indexed references, and a snowball issues one of these
    requests per seed per direction. Counting those as failures would feed the
    circuit breaker until it skipped OpenAlex — for keyword search too.
    """
    rec.succeed(http_status=http_status)


def work_id(paper: dict) -> str:
    """The OpenAlex work id already present on *paper*, or ''.

    Costs no request. A paper that came from OpenAlex carries its work id in
    ``id``; anything else has to be resolved by DOI first.
    """
    for field in ("openalex_id", "id", "url"):
        value = paper.get(field)
        if not isinstance(value, str) or not value:
            continue
        # Only where the value *is* an OpenAlex reference. Scanning any string
        # for a W-shaped token would read a work id out of an unrelated URL.
        candidate = value.strip()
        if "openalex.org" not in candidate and not _WORK_ID_RE.fullmatch(candidate):
            continue
        match = _WORK_ID_RE.search(candidate)
        if match:
            return match.group(0)
    return ""


async def resolve_work_id(paper: dict) -> str:
    """The OpenAlex work id for *paper*, looking it up by DOI when needed.

    Returns '' when the paper carries neither an OpenAlex id nor a DOI OpenAlex
    holds, which is the honest answer rather than a gap to paper over:

    * A title search is not an option. `filter=title.search:` answers HTTP 503
      ("not enough resources were available to cover 100% of the index") —
      checked 2026-09-25, the same failure that got CORE removed as a source.
    * An arXiv-only paper is often unreachable here: OpenAlex does not index
      arXiv's DataCite DOI for a paper it holds under the published record, so
      ``10.48550/arXiv.1706.03762`` resolves to nothing. This is exactly why
      snowballing traverses two graphs — Semantic Scholar answers that seed
      under ``ARXIV:1706.03762``.
    """
    existing = work_id(paper)
    if existing:
        return existing

    doi = paper_doi(paper)
    if not doi:
        return ""

    headers, params = _request_context()
    params["select"] = "id"
    async with track_call("OpenAlex", "resolve") as rec:
        try:
            async with pooled_client(headers=headers) as client:
                response = await client.get(
                    f"{WORKS_URL}/https://doi.org/{doi}", params=params, timeout=8.0
                )
                if response.status_code == 404:
                    # Not indexed. A real answer about the paper, not a broken
                    # API — see _record_empty on why that distinction matters.
                    _record_empty(rec, 404)
                    return ""
                response.raise_for_status()
                resolved = _WORK_ID_RE.search(response.json().get("id") or "")
                if not resolved:
                    _record_empty(rec, response.status_code)
                    return ""
                rec.succeed(http_status=response.status_code, items=1)
                return resolved.group(0)
        except Exception as e:
            rec.fail(error=str(e))
            logger.warning("OpenAlex could not resolve DOI %s: %s", doi, e)
            return ""


async def fetch_related(paper: dict, direction: str, limit: int = 25) -> list[dict]:
    """Works cited by *paper* (``backward``) or citing it (``forward``).

    Sorted by citation count so a paper with 4000 citing works contributes its
    most influential ones rather than an arbitrary page of them.
    """
    filter_key = _DIRECTION_FILTER.get(direction)
    if filter_key is None:
        raise ValueError(f"direction must be one of {sorted(_DIRECTION_FILTER)}, got {direction!r}")

    identifier = await resolve_work_id(paper)
    if not identifier:
        return []

    headers, params = _request_context()
    params.update({
        "filter": f"{filter_key}:{identifier}",
        "per-page": max(1, min(limit, 200)),
        "select": _SELECT,
        "sort": "cited_by_count:desc",
    })

    async with track_call("OpenAlex", f"snowball-{direction}") as rec:
        try:
            async with pooled_client(headers=headers) as client:

                async def attempt():
                    response = await client.get(WORKS_URL, params=params, timeout=12.0)
                    response.raise_for_status()
                    return response

                response = await with_retry(attempt, name="OpenAlex", attempts=2, budget=3.0)
                papers = [_normalize_work(item) for item in response.json().get("results", [])]
                if papers:
                    rec.succeed(http_status=response.status_code, items=len(papers))
                else:
                    _record_empty(rec, response.status_code)
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error("OpenAlex %s citation fetch failed: %s", direction, e)
            # Raised, not swallowed into []. This is the one caller
            # (`integrations/snowball.py`), it catches per request, and it has
            # to distinguish "this graph knows no edges for your seeds" from
            # "this graph is down" — returning [] makes an outage look like an
            # answer in the source panel. `search_papers` above still returns []
            # because the fan-out there treats an empty list as its own signal.
            raise
