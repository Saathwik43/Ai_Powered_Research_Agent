import os
import httpx
import logging

logger = logging.getLogger(__name__)

CORE_API_URL = "https://api.core.ac.uk/v3/search/works"

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
    
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(CORE_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            results = data.get("results", [])
            papers = []
            for item in results:
                authors_info = item.get("authors", [])
                authors = [a.get("name") for a in authors_info if a.get("name")]
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                
                papers.append({
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "authors": author_str,
                    "year": str(item.get("yearPublished", "Unknown")),
                    "abstract": item.get("abstract", ""),
                    "url": item.get("downloadUrl", ""),
                    "pdf_url": item.get("downloadUrl", ""),
                    "citations": item.get("citationCount", 0),
                    "source": "CORE"
                })
            return papers
    except Exception as e:
        logger.error(f"CORE search error: {e}")
        return []
