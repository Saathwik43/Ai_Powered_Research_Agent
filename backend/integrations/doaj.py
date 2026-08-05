import httpx
import logging
import urllib.parse

logger = logging.getLogger(__name__)

DOAJ_API_URL = "https://doaj.org/api/search/articles"


async def search_papers(query: str, limit: int = 15) -> list:
    """DOAJ (Directory of Open Access Journals) — free, no API key required.
    All results are verified open-access, so everything returned is legally
    downloadable — good quality signal on top of raw coverage."""
    encoded_query = urllib.parse.quote(query)
    url = f"{DOAJ_API_URL}/{encoded_query}"
    params = {"pageSize": limit}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", [])
            papers = []
            for item in results:
                bibjson = item.get("bibjson", {})
                authors_info = bibjson.get("author", []) or []
                authors = [a.get("name") for a in authors_info if a.get("name")]
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

                doi = ""
                for ident in bibjson.get("identifier", []) or []:
                    if ident.get("type") == "doi":
                        doi = ident.get("id", "")
                        break

                link_url = ""
                for link in bibjson.get("link", []) or []:
                    if link.get("type") == "fulltext":
                        link_url = link.get("url", "")
                        break

                papers.append({
                    "id": item.get("id", ""),
                    "title": bibjson.get("title", ""),
                    "authors": author_str,
                    "year": str(bibjson.get("year", "Unknown")),
                    "abstract": bibjson.get("abstract", "") or "",
                    "url": link_url or (f"https://doi.org/{doi}" if doi else ""),
                    "pdf_url": link_url,
                    "citations": 0,  # DOAJ doesn't provide citation counts
                    "source": "DOAJ",
                })
            return [p for p in papers if p["title"]]
    except Exception as e:
        logger.error(f"DOAJ search error: {e}")
        return []
