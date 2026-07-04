"""
integrations/unpaywall.py
-------------------------
Unpaywall DOI → open-access URL enrichment.

Not a search source — called *after* dedup in search_all() to add ``oa_url``
fields to papers that have a resolvable DOI.

API
~~~
GET https://api.unpaywall.org/v2/{doi}?email={email}

- Free, no API key — identifies polite-pool users by ``email`` param.
- Uses the existing ``CROSSREF_MAILTO`` env var.
- 3s timeout per lookup; failures are silently swallowed (best-effort).

Design constraints
~~~~~~~~~~~~~~~~~~
- Async httpx only, no ``requests``.
- Parallel lookups via asyncio.gather (return_exceptions=True).
- Never raises; always returns the input list (possibly enriched).
"""

import os
import re
import asyncio
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
_EMAIL = os.getenv("CROSSREF_MAILTO", "")
_CALL_TIMEOUT = 3.0


def _extract_doi(paper: dict) -> str | None:
    """
    Try to extract a clean DOI string from a paper dict.

    Sources that carry DOIs:
      - Crossref: ``id`` field is a raw DOI string (e.g. "10.1234/...")
      - OpenAlex: ``url`` field is a full DOI URL (e.g. "https://doi.org/10.1234/...")
      - Others: may have ``doi`` field directly

    Returns None if no DOI can be extracted.
    """
    # Direct doi field
    doi = paper.get("doi", "")
    if doi:
        # Strip URL prefix if present
        doi = re.sub(r"^https?://doi\.org/", "", doi)
        if doi.startswith("10."):
            return doi

    # Crossref stores DOI in id field
    paper_id = paper.get("id", "")
    if isinstance(paper_id, str) and paper_id.startswith("10."):
        return paper_id

    # OpenAlex stores DOI URL in url field
    url = paper.get("url", "")
    if isinstance(url, str) and "doi.org/" in url:
        doi = re.sub(r"^https?://doi\.org/", "", url)
        if doi.startswith("10."):
            return doi

    return None


async def _lookup_oa(client: httpx.AsyncClient, doi: str) -> dict | None:
    """
    Look up a single DOI on Unpaywall and return the best OA location, or None.
    """
    if not _EMAIL:
        return None
    try:
        resp = await client.get(f"{_UNPAYWALL_BASE}/{doi}", params={"email": _EMAIL})
        if resp.status_code != 200:
            return None
        data = resp.json()
        # best_oa_location is the primary open-access link
        best_oa = data.get("best_oa_location")
        if best_oa and best_oa.get("url"):
            return {
                "oa_url": best_oa["url"],
                "oa_host_type": best_oa.get("host_type", ""),
            }
        return None
    except Exception as exc:
        logger.debug(f"Unpaywall lookup failed for {doi}: {exc}")
        return None


async def enrich_papers_with_oa(papers: list) -> list:
    """
    Enrich a list of paper dicts with ``oa_url`` fields from Unpaywall.

    Best-effort: papers without DOIs or failed lookups are returned unchanged.
    All lookups run in parallel with a per-call 3s timeout.

    Parameters
    ----------
    papers : list[dict]
        Paper dicts as returned by search_all() after dedup + ranking.

    Returns
    -------
    list[dict]
        Same list with ``oa_url`` added where an open-access link was found.
    """
    if not _EMAIL:
        logger.debug("Unpaywall enrichment skipped: CROSSREF_MAILTO not set.")
        return papers

    # Build (index, doi) pairs for papers that have extractable DOIs
    doi_pairs: list[tuple[int, str]] = []
    for i, paper in enumerate(papers):
        doi = _extract_doi(paper)
        if doi:
            doi_pairs.append((i, doi))

    if not doi_pairs:
        return papers

    async with httpx.AsyncClient(timeout=_CALL_TIMEOUT) as client:
        tasks = [_lookup_oa(client, doi) for _, doi in doi_pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched_count = 0
    for (idx, doi), result in zip(doi_pairs, results):
        if isinstance(result, Exception):
            logger.debug(f"Unpaywall gather exception for {doi}: {result}")
            continue
        if result and result.get("oa_url"):
            papers[idx]["oa_url"] = result["oa_url"]
            enriched_count += 1

    if enriched_count:
        logger.info(f"Unpaywall enriched {enriched_count}/{len(doi_pairs)} papers with OA links.")

    return papers
