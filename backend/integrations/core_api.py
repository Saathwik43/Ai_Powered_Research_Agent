import os
import httpx
from integrations.http_client import pooled_client
import logging
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

CORE_API_URL = "https://api.core.ac.uk/v3/search/works/"

async def search_papers(query: str, limit: int = 15) -> list:
    api_key = os.getenv("CORE_API_KEY")
    if not api_key:
        logger.warning("CORE_API_KEY not set. Skipping CORE search.")
        return []

    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    params = {
        "q": query,
        "limit": limit
    }

    async with track_call("CORE", "search") as rec:
        try:
            async with pooled_client(headers=headers, timeout=8.0) as client:
                resp = await client.get(CORE_API_URL, params=params)
                if resp.status_code == 401:
                    rec.fail(http_status=401, error="CORE API key rejected or trial expired")
                    logger.error("CORE search error: HTTP 401 — key rejected or trial expired")
                    return []
                resp.raise_for_status()
                data = resp.json()
                
                results = data.get("results", [])
                papers = []
                for item in results:
                    authors_info = item.get("authors", [])
                    authors = [a.get("name") for a in authors_info if a.get("name")]
                    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                    doi = ""
                    for ident in item.get("identifiers") or []:
                        if isinstance(ident, str) and ident.startswith("10."):
                            doi = ident
                            break
                        if isinstance(ident, dict) and (ident.get("type") or "").lower() == "doi":
                            doi = ident.get("identifier") or ident.get("value") or ""
                            break
                    
                    papers.append({
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "authors": author_str,
                        "year": str(item.get("yearPublished", "Unknown")),
                        "abstract": item.get("abstract", ""),
                        "url": item.get("downloadUrl", ""),
                        "pdf_url": item.get("downloadUrl", ""),
                        "doi": doi,
                        "citations": item.get("citationCount", 0),
                        "source": "CORE"
                    })
                rec.succeed(http_status=resp.status_code, items=len(papers))
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"CORE search error: {e}")
            return []
