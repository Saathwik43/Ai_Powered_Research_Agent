import os
import httpx
from dotenv import load_dotenv

load_dotenv()

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

async def search_papers(query: str, limit: int = 8) -> list:
    """
    Search Semantic Scholar by keyword.
    Returns normalized paper dicts.
    """
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
        
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,authors,year,citationCount,abstract,url,openAccessPdf"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(S2_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            papers = []
            for item in data.get("data", []):
                # Process authors
                authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                if not author_str:
                    author_str = "Unknown Authors"
                    
                # PDF link
                pdf_url = ""
                oa_pdf = item.get("openAccessPdf")
                if oa_pdf and isinstance(oa_pdf, dict):
                    pdf_url = oa_pdf.get("url", "")
                    
                paper = {
                    "id": item.get("paperId", ""),
                    "title": item.get("title", "Untitled"),
                    "authors": author_str,
                    "year": str(item.get("year", "Unknown")),
                    "citations": item.get("citationCount", 0),
                    "abstract": item.get("abstract") or "No abstract available.",
                    "url": item.get("url", ""),
                    "pdf_url": pdf_url,
                    "source": "Semantic Scholar"
                }
                papers.append(paper)
            return papers
    except Exception as e:
        print(f"Semantic Scholar Error: {e}")
        return []
