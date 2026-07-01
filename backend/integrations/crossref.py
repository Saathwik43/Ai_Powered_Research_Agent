import os
import asyncio
import requests
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
        response = requests.get(url, headers=headers)
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
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        return data.get("message", {}).get("items", [])
    except Exception as e:
        logger.error(f"Error searching Crossref journals for {query}: {e}")
        return []
