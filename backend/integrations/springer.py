import os
import httpx
import logging

logger = logging.getLogger(__name__)

SPRINGER_API_URL = "https://api.springernature.com/meta/v2/json"

async def search_papers(query: str, limit: int = 15) -> list:
    api_key = os.getenv("SPRINGER_META_API_KEY")
    if not api_key:
        logger.warning("SPRINGER_META_API_KEY not set. Skipping Springer search.")
        return []

    params = {
        "q": f"keyword:{query}",
        "api_key": api_key,
        "p": limit
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SPRINGER_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            records = data.get("records", [])
            papers = []
            for item in records:
                # Find PDF url if available
                pdf_url = ""
                for url_info in item.get("url", []):
                    if url_info.get("format") == "pdf":
                        pdf_url = url_info.get("value", "")
                        break
                
                # Format authors
                creators = item.get("creators", [])
                authors = [c.get("creator") for c in creators if c.get("creator")]
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                
                pub_date = item.get("publicationDate", "")
                year = pub_date[:4] if pub_date else "Unknown"
                
                papers.append({
                    "id": item.get("identifier", ""),
                    "title": item.get("title", ""),
                    "authors": author_str,
                    "year": year,
                    "abstract": item.get("abstract", ""),
                    "url": pdf_url or item.get("url", [{"value":""}])[0].get("value", ""),
                    "pdf_url": pdf_url,
                    "citations": 0,
                    "source": "Springer"
                })
            return papers
    except Exception as e:
        logger.error(f"Springer search error: {e}")
        return []
