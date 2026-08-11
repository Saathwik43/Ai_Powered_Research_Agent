import os
import httpx
import logging
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

BASE_API_URL = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"


async def search_papers(query: str, limit: int = 15) -> list:
    """BASE (Bielefeld Academic Search Engine) — free, no API key required.
    Strong coverage of EU institutional repositories, theses, and grey literature
    that OpenAlex/Crossref sometimes miss."""
    email = os.getenv("CROSSREF_MAILTO", "")
    headers = {
        "User-Agent": (
            f"ResearchAgent/1.0 (mailto:{email}; academic literature search)"
            if email else "ResearchAgent/1.0 (academic literature search)"
        )
    }
    params = {
        "func": "PerformSearchRequest",
        "query": query,
        "hits": limit,
        "format": "json",
    }

    async with track_call("BASE", "search") as rec:
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                resp = await client.get(BASE_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            if data.get("error"):
                rec.fail(http_status=resp.status_code, error=str(data["error"]))
                logger.error("BASE search error: %s", data["error"])
                return []

            docs = data.get("response", {}).get("docs", [])
            papers = []
            for item in docs:
                authors = item.get("dccreator", []) or []
                if isinstance(authors, str):
                    authors = [authors]
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

                links = item.get("dclink", []) or []
                if isinstance(links, str):
                    links = [links]
                url = links[0] if links else ""

                year = item.get("dcyear", "Unknown")
                if isinstance(year, list):
                    year = year[0] if year else "Unknown"

                raw_title = item.get("dctitle") or ""
                if isinstance(raw_title, list):
                    raw_title = raw_title[0] if raw_title else ""
                papers.append({
                    "id": item.get("dcidentifier", [""])[0] if item.get("dcidentifier") else "",
                    "title": str(raw_title).strip(),
                    "authors": author_str,
                    "year": str(year),
                    "abstract": item.get("dcabstract", "") or "",
                    "url": url,
                    "pdf_url": url,
                    "citations": 0,
                    "source": "BASE",
                })
            papers = [p for p in papers if p["title"]]
            rec.succeed(http_status=resp.status_code, items=len(papers))
            return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"BASE search error: {e}")
            return []
