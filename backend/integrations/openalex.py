import os
import httpx
from integrations.http_client import pooled_client
import logging
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

logger = logging.getLogger(__name__)

async def search_papers(query: str, limit: int = 5):
    """
    Searches the OpenAlex API for research papers matching the query.
    Extracts title, authors, year, citation count, and abstract.
    """
    email = os.getenv("CROSSREF_MAILTO", "")
    headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{email})" if email else "ResearchAgent/1.0"}
    
    url = "https://api.openalex.org/works"
    params = {
        "search": query,
        "per-page": limit,
        "select": "id,doi,title,publication_year,cited_by_count,authorships,abstract_inverted_index",
    }
    if email:
        params["mailto"] = email
    api_key = os.getenv("OPENALEX_API_KEY", "")
    if api_key:
        params["api_key"] = api_key

    async with track_call("OpenAlex", "search") as rec:
        try:
            async with pooled_client(headers=headers) as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                papers = []
                for item in data.get("results", []):
                    authorships = item.get("authorships", [])
                    authors = [a.get("author", {}).get("display_name", "") for a in authorships if a.get("author")]
                    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                    if not author_str:
                        author_str = "Unknown Authors"

                    abstract_inverted = item.get("abstract_inverted_index")
                    abstract = "No abstract available"
                    if abstract_inverted:
                        word_index = []
                        for word, positions in abstract_inverted.items():
                            for pos in positions:
                                word_index.append((pos, word))
                        word_index.sort(key=lambda x: x[0])
                        abstract = " ".join([word for pos, word in word_index])

                    doi_url = item.get("doi") or ""
                    doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "") if doi_url else ""
                    paper = {
                        "id": item.get("id"),
                        "title": item.get("title", "Untitled"),
                        "authors": author_str,
                        "year": item.get("publication_year", "Unknown"),
                        "citations": item.get("cited_by_count", 0),
                        "abstract": abstract,
                        "url": doi_url or item.get("id"),
                        "doi": doi,
                        "source": "OpenAlex",
                    }
                    papers.append(paper)
                rec.succeed(http_status=response.status_code, items=len(papers))
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"OpenAlex Error: {e}")
            return []
