import os
import re
import asyncio
import httpx
from integrations.http_client import pooled_client
import logging
import urllib.parse
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

_JATS_RE = re.compile(r"</?jats:[^>]+>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_jats(text: str) -> str:
    if not text or "<" not in text:
        return text or "No abstract available"
    cleaned = _JATS_RE.sub("", text)
    cleaned = _TAG_RE.sub("", cleaned)
    return " ".join(cleaned.split()) or "No abstract available"

MAILTO = os.getenv("CROSSREF_MAILTO", "your_email@example.com")
CROSSREF_BASE_URL = "https://api.crossref.org/journals"

logger = logging.getLogger(__name__)

def _bibliographic_fields(item: dict) -> dict:
    """Venue, entry type and page numbers for the bibliography (2.8).

    Crossref is the richest source here — it is the registration agency, so its
    `type` is authoritative rather than inferred, and it is the only one of our
    sources that reliably reports publisher and page ranges. Keys with no real
    value are omitted so the dedupe merge can fill them from another record.
    """
    out = {}

    container = item.get("container-title") or []
    venue = (container[0] if container else "").strip()
    if venue:
        out["venue"] = venue

    # "journal-article", "proceedings-article", "book-chapter", "posted-content"...
    work_type = (item.get("type") or "").strip()
    if work_type:
        out["type"] = work_type
    # Distinguishes a preprint from any other `posted-content` record.
    subtype = (item.get("subtype") or "").strip()
    if subtype:
        out["subtype"] = subtype

    publisher = (item.get("publisher") or "").strip()
    if publisher:
        out["publisher"] = publisher
    volume = str(item.get("volume") or "").strip()
    if volume:
        out["volume"] = volume
    issue = str(item.get("issue") or "").strip()
    if issue:
        out["issue"] = issue
    # Crossref writes ranges as "12-34"; BibTeX wants an en-dash range.
    page = str(item.get("page") or "").strip()
    if page:
        out["pages"] = page.replace("-", "--") if "--" not in page else page

    return out


async def get_venue_metadata(issn: str):
    """
    Queries Crossref REST API for venue metadata (using polite pool).
    """
    try:
        url = f"{CROSSREF_BASE_URL}/{issn}"
        headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{MAILTO})"}
        
        # Enforce rate limits by offloading to another thread or just async sleep
        await asyncio.sleep(0.5) 
        async with pooled_client() as client:
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
        async with pooled_client() as client:
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
        
        async with track_call("Crossref", "search") as rec:
            async with pooled_client(timeout=12.0) as client:
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

                doi = item.get("DOI", "")
                paper = {
                    "id": doi,
                    "title": title,
                    "authors": author_str,
                    "year": str(published) if published else "Unknown",
                    "citations": item.get("is-referenced-by-count", 0),
                    "abstract": _strip_jats(item.get("abstract") or "No abstract available"),
                    "url": item.get("URL", ""),
                    "doi": doi,
                    "source": "Crossref"
                }
                paper.update(_bibliographic_fields(item))
                papers.append(paper)
            rec.succeed(http_status=response.status_code, items=len(papers))
            return papers
    except Exception as e:
        logger.error(f"Error searching Crossref works for {query}: {e}")
        return []
