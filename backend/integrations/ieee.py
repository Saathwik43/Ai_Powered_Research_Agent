import os
import httpx
import logging

logger = logging.getLogger(__name__)

IEEE_API_URL = "http://ieeexploreapi.ieee.org/api/v1/search/articles"

async def search_papers(query: str, limit: int = 15) -> list:
    api_key = os.getenv("IEEE_API_KEY")
    if not api_key:
        logger.warning("IEEE_API_KEY not set. Skipping IEEE search.")
        return []

    params = {
        "querytext": query,
        "apikey": api_key,
        "max_records": limit
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(IEEE_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            articles = data.get("articles", [])
            papers = []
            for item in articles:
                # Format authors
                authors_info = item.get("authors", {}).get("authors", [])
                authors = [a.get("full_name") for a in authors_info if a.get("full_name")]
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                
                pdf_url = item.get("pdf_url", "")
                url = item.get("article_url", pdf_url)
                
                papers.append({
                    "id": item.get("article_number", ""),
                    "title": item.get("title", ""),
                    "authors": author_str,
                    "year": str(item.get("publication_year", "Unknown")),
                    "abstract": item.get("abstract", ""),
                    "url": url,
                    "pdf_url": pdf_url,
                    "citations": item.get("citing_paper_count", 0),
                    "source": "IEEE"
                })
            return papers
    except Exception as e:
        logger.error(f"IEEE search error: {e}")
        return []
