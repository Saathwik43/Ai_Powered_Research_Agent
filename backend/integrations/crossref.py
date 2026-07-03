import os
import asyncio
import httpx
import logging
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

MAILTO = os.getenv("CROSSREF_MAILTO", "your_email@example.com")
CROSSREF_BASE_URL = "https://api.crossref.org/journals"

logger = logging.getLogger(__name__)

async def get_venue_metadata(issn: str):
    """
    Queries Crossref REST API for venue metadata (using polite pool).
    """
    try:
        url = f"{CROSSREF_BASE_URL}/{issn}"
        headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{MAILTO})"}
        
        # Enforce rate limits by offloading to another thread or just async sleep
        await asyncio.sleep(0.5) 
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {})
    except Exception as e:
        logger.error(f"Error fetching Crossref metadata for ISSN {issn}: {e}")
        return {}

async def search_journals(query: str):
    """
    Search for journals on Crossref.
    """
    try:
        url = f"{CROSSREF_BASE_URL}?query={query}&rows=3"
        headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{MAILTO})"}
        
        await asyncio.sleep(0.5) 
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("items", [])
    except Exception as e:
        logger.error(f"Error searching Crossref journals for {query}: {e}")
        return []

async def search_works(query: str, limit: int = 8) -> list:
    """
    Search for works (papers) on Crossref.
    """
    try:
        url = f"https://api.crossref.org/works"
        params = {
            "query": query,
            "rows": limit
        }
        headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{MAILTO})"}
        
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
        items = data.get("message", {}).get("items", [])
        
        papers = []
        for item in items:
            title = item.get("title", ["Untitled"])[0] if item.get("title") else "Untitled"
            
            authors_list = []
            for author in item.get("author", []):
                name = f"{author.get('given', '')} {author.get('family', '')}".strip()
                if name:
                    authors_list.append(name)
            author_str = ", ".join(authors_list[:3]) + (" et al." if len(authors_list) > 3 else "")
            if not author_str:
                author_str = "Unknown Authors"
            
            published = item.get("published-print", {}).get("date-parts", [[None]])[0][0]
            if not published:
                published = item.get("created", {}).get("date-parts", [[None]])[0][0]
            
            papers.append({
                "id": item.get("DOI", ""),
                "title": title,
                "authors": author_str,
                "year": str(published) if published else "Unknown",
                "citations": item.get("is-referenced-by-count", 0),
                "abstract": item.get("abstract", "No abstract available"),
                "url": item.get("URL", ""),
                "source": "Crossref"
            })
        return papers
    except Exception as e:
        logger.error(f"Error searching Crossref works for {query}: {e}")
        return []
