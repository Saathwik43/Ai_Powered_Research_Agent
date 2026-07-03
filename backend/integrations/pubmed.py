"""
integrations/pubmed.py
----------------------
PubMed E-utilities integration for search_all().

Two-call pattern
~~~~~~~~~~~~~~~~
1. esearch.fcgi — keyword → list of PMIDs   (timeout=5s each call)
2. esummary.fcgi — PMIDs → metadata         (timeout=5s each call)

Abort-early guard
~~~~~~~~~~~~~~~~~
If esearch itself takes longer than 3 seconds we abandon the request and
return [] immediately rather than proceeding to esummary.  This prevents a
slow first call guaranteeing a slow second call on top of it, keeping
worst-case contribution to search_all() bounded.

Rate limits
~~~~~~~~~~~
Unauthenticated: 3 req/s.  With PUBMED_API_KEY: 10 req/s (NCBI policy).
The key is passed as the ``api_key`` query parameter on every call.

Design constraints (lessons from CrossRef slowdown bug)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- No asyncio.sleep() in the hot path.
- No requests library — async httpx only.
- Per-call timeout=5s, matching other providers.
- All exceptions caught; always return list (never raise).
"""

import os
import time
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PUBMED_API_KEY: str = os.getenv("PUBMED_API_KEY", "")
_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Per-call HTTP timeout (seconds) — matches all other providers.
_CALL_TIMEOUT = 5.0

# If esearch alone exceeds this threshold we abort rather than proceed to
# esummary, keeping total worst-case latency bounded.
_ESEARCH_ABORT_AFTER = 3.0


def _base_params() -> dict:
    """Common query parameters for all E-utilities calls."""
    params: dict = {"db": "pubmed", "retmode": "json"}
    if PUBMED_API_KEY:
        params["api_key"] = PUBMED_API_KEY
    return params


async def _esearch(client: httpx.AsyncClient, query: str, limit: int) -> list[str]:
    """
    Call esearch.fcgi and return a list of PMIDs (strings).

    Returns [] on any error or empty result.
    """
    params = _base_params()
    params.update({
        "term": query,
        "retmax": limit,
        "sort": "relevance",
    })
    try:
        resp = await client.get(_ESEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        pmids: list = data.get("esearchresult", {}).get("idlist", [])
        return [str(p) for p in pmids]
    except Exception as exc:
        logger.error(f"PubMed esearch error: {exc}")
        return []


async def _esummary(client: httpx.AsyncClient, pmids: list[str]) -> list[dict]:
    """
    Call esummary.fcgi for the given PMIDs and return normalised paper dicts.

    Returns [] on any error.
    """
    params = _base_params()
    params["id"] = ",".join(pmids)

    try:
        resp = await client.get(_ESUMMARY_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"PubMed esummary error: {exc}")
        return []

    result_block = data.get("result", {})
    uids: list = result_block.get("uids", [])

    papers: list[dict] = []
    for uid in uids:
        item = result_block.get(str(uid), {})
        if not item or item.get("error"):
            continue

        # Title
        title: str = item.get("title", "Untitled").rstrip(".")
        if not title:
            title = "Untitled"

        # Authors — list of {"name": "Surname FM"} objects
        raw_authors: list = item.get("authors", [])
        author_names = [a.get("name", "") for a in raw_authors if a.get("name")]
        author_str = ", ".join(author_names[:3])
        if len(author_names) > 3:
            author_str += " et al."
        if not author_str:
            author_str = "Unknown Authors"

        # Year — esummary returns pubdate like "2023 Jan" or "2023"
        pub_date: str = item.get("pubdate", "")
        year: str = pub_date.split()[0] if pub_date else "Unknown"
        if not year.isdigit():
            year = "Unknown"

        # Abstract — esummary does NOT return abstracts; mark accordingly.
        # Fetching per-paper abstracts would require N extra calls and is
        # cost-prohibitive at search time.
        abstract: str = "Abstract not available via PubMed summary API."

        # URL — canonical PubMed page
        url: str = f"https://pubmed.ncbi.nlm.nih.gov/{uid}/"

        # Source journal (bonus metadata)
        source_journal: str = item.get("source", "")

        papers.append({
            "id": f"pmid:{uid}",
            "title": title,
            "authors": author_str,
            "year": year,
            "citations": 0,           # esummary doesn't provide citation count
            "abstract": abstract,
            "url": url,
            "source": "PubMed",
            "journal": source_journal,
        })

    return papers


async def search_papers(query: str, limit: int = 8) -> list[dict]:
    """
    Search PubMed via E-utilities and return normalised paper dicts.

    Applies a two-call esearch → esummary pattern with:
    - 5s timeout per HTTP call (matches all other providers).
    - Abort-early guard: if esearch takes >3s the function returns [] rather
      than proceeding to esummary, bounding total worst-case latency.
    - PUBMED_API_KEY used when set (10 req/s vs 3 req/s unauthenticated).
    - No asyncio.sleep(), no requests library.

    Always returns a list (never raises).
    """
    if not query or not query.strip():
        return []

    t0 = time.monotonic()

    # ── Call 1: esearch ──────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_CALL_TIMEOUT) as client:
            pmids = await _esearch(client, query.strip(), limit)
    except Exception as exc:
        logger.error(f"PubMed search_papers unexpected error in esearch phase: {exc}")
        return []

    elapsed_after_esearch = time.monotonic() - t0

    # Abort-early guard: if esearch itself was slow, don't pile a second call on top
    if elapsed_after_esearch > _ESEARCH_ABORT_AFTER:
        logger.warning(
            f"PubMed esearch took {elapsed_after_esearch:.2f}s "
            f"(>{_ESEARCH_ABORT_AFTER}s threshold); skipping esummary to protect ceiling."
        )
        return []

    if not pmids:
        logger.debug("PubMed esearch returned no PMIDs for query: %r", query)
        return []

    # ── Call 2: esummary ─────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_CALL_TIMEOUT) as client:
            papers = await _esummary(client, pmids)
    except Exception as exc:
        logger.error(f"PubMed search_papers unexpected error in esummary phase: {exc}")
        return []

    total_elapsed = time.monotonic() - t0
    logger.info(
        f"PubMed returned {len(papers)} papers in {total_elapsed:.2f}s "
        f"(esearch: {elapsed_after_esearch:.2f}s, "
        f"esummary: {total_elapsed - elapsed_after_esearch:.2f}s)"
    )
    return papers
