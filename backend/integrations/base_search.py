import httpx
import logging

logger = logging.getLogger(__name__)

BASE_API_URL = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"


async def search_papers(query: str, limit: int = 15) -> list:
    """BASE (Bielefeld Academic Search Engine) — free, no API key required.
    Strong coverage of EU institutional repositories, theses, and grey literature
    that OpenAlex/Crossref sometimes miss."""
    params = {
        "func": "PerformSearchRequest",
        "query": query,
        "hits": limit,
        "format": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(BASE_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            docs = data.get("response", {}).get("docs", [])
            papers = []
            for item in docs:
                authors = item.get("dccreator", []) or []
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

                links = item.get("dclink", []) or []
                url = links[0] if links else ""

                year = item.get("dcyear", "Unknown")

                papers.append({
                    "id": item.get("dcidentifier", [""])[0] if item.get("dcidentifier") else "",
                    "title": (item.get("dctitle") or "").strip(),
                    "authors": author_str,
                    "year": str(year),
                    "abstract": item.get("dcabstract", "") or "",
                    "url": url,
                    "pdf_url": url,
                    "citations": 0,  # BASE doesn't provide citation counts
                    "source": "BASE",
                })
            return [p for p in papers if p["title"]]
    except Exception as e:
        logger.error(f"BASE search error: {e}")
        return []
