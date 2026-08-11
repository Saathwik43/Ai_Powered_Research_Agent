import os
import asyncio
import httpx
from integrations.http_client import pooled_client
import logging
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
_FIELDS = "title,authors,year,citationCount,abstract,url,openAccessPdf,externalIds"

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

    return {
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
                for attempt in range(3):
                    resp = await client.get(S2_SEARCH_URL, params=params, headers=headers)
                    last_status = resp.status_code
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After", "")
                        try:
                            wait = min(float(retry_after), 4.0)
                        except (TypeError, ValueError):
                            wait = 1.5 * (attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
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
