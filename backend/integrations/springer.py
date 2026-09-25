import os
import httpx
from integrations.http_client import pooled_client
import logging
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

SPRINGER_API_URL = "https://api.springernature.com/meta/v2/json"


def _springer_doi(item: dict) -> str:
    ident = item.get("identifier") or item.get("doi") or ""
    if isinstance(ident, str):
        ident = ident.replace("doi:", "").replace("https://doi.org/", "").replace("http://dx.doi.org/", "").strip()
        if ident.startswith("10."):
            return ident
    return ""


async def search_papers(query: str, limit: int = 15) -> list:
    api_key = os.getenv("SPRINGER_META_API_KEY")
    if not api_key:
        logger.warning("SPRINGER_META_API_KEY not set. Skipping Springer search.")
        return []

    params = {
        "q": query,
        "api_key": api_key,
        "p": limit
    }

    async with track_call("Springer Nature", "search") as rec:
        try:
            async with pooled_client(timeout=15.0) as client:
                resp = await client.get(SPRINGER_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                records = data.get("records", [])
                papers = []
                for item in records:
                    pdf_url = ""
                    for url_info in item.get("url", []):
                        if url_info.get("format") == "pdf":
                            pdf_url = url_info.get("value", "")
                            break
                    
                    creators = item.get("creators", [])
                    authors = [c.get("creator") for c in creators if c.get("creator")]
                    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                    
                    pub_date = item.get("publicationDate", "")
                    year = pub_date[:4] if pub_date else "Unknown"
                    doi = _springer_doi(item)
                    
                    paper = {
                        "id": item.get("identifier", ""),
                        "title": item.get("title", ""),
                        "authors": author_str,
                        "year": year,
                        "abstract": item.get("abstract", ""),
                        "url": pdf_url or item.get("url", [{"value":""}])[0].get("value", ""),
                        "pdf_url": pdf_url,
                        "doi": doi,
                        "citations": 0,
                        "source": "Springer"
                    }

                    # Springer's metadata already distinguishes a journal
                    # article from a book chapter; `publicationName` is the
                    # venue rather than the search engine's name (2.8).
                    venue = (item.get("publicationName") or "").strip()
                    if venue:
                        paper["venue"] = venue
                    content_type = str(item.get("contentType") or "").strip().lower()
                    if content_type == "article":
                        paper["type"] = "journal-article"
                    elif content_type == "chapter":
                        paper["type"] = "book-chapter"
                    elif content_type == "book":
                        paper["type"] = "book"
                    publisher = (item.get("publisher") or "").strip()
                    if publisher:
                        paper["publisher"] = publisher
                    volume = str(item.get("volume") or "").strip()
                    if volume:
                        paper["volume"] = volume
                    issue = str(item.get("number") or "").strip()
                    if issue:
                        paper["issue"] = issue
                    start = str(item.get("startingPage") or "").strip()
                    end = str(item.get("endingPage") or "").strip()
                    if start:
                        paper["pages"] = f"{start}--{end}" if end and end != start else start

                    papers.append(paper)
                rec.succeed(http_status=resp.status_code, items=len(papers))
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"Springer search error: {e}")
            return []
