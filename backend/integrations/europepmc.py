import httpx
import logging
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


async def search_papers(query: str, limit: int = 15) -> list:
    """Europe PMC — free, no API key required. Strong biomedical/life-sciences
    coverage, includes full-text OA links via PMC. Good complement to PubMed
    when esummary is slow/skipped."""
    params = {
        "query": query,
        "format": "json",
        "pageSize": limit,
        "resultType": "core",  # needed to get abstractText + authorList
    }

    async with track_call("Europe PMC", "search") as rec:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(EUROPEPMC_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = data.get("resultList", {}).get("result", [])
            papers = []
            for item in results:
                author_str = item.get("authorString", "") or ""

                doi = item.get("doi", "")
                pmcid = item.get("pmcid", "")
                url = f"https://doi.org/{doi}" if doi else (
                    f"https://europepmc.org/article/PMC/{pmcid}" if pmcid else ""
                )
                pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""

                papers.append({
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "authors": author_str,
                    "year": str(item.get("pubYear", "Unknown")),
                    "abstract": item.get("abstractText", "") or "",
                    "url": url,
                    "pdf_url": pdf_url,
                    "doi": doi,
                    "citations": item.get("citedByCount", 0) or 0,
                    "source": "EuropePMC",
                })
            papers = [p for p in papers if p["title"]]
            rec.succeed(http_status=resp.status_code, items=len(papers))
            return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"Europe PMC search error: {e}")
            return []
