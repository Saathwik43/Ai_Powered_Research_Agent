import httpx
import xml.etree.ElementTree as ET
from datetime import datetime

ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"
ARXIV_RSS_BASE = "https://rss.arxiv.org/rss"

# Atom namespace
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

# Map friendly category names to arXiv codes
CATEGORY_MAP = {
    "artificial intelligence": "cs.AI",
    "machine learning": "cs.LG",
    "computer vision": "cs.CV",
    "nlp": "cs.CL",
    "natural language processing": "cs.CL",
    "cybersecurity": "cs.CR",
    "data science": "cs.DS",
    "quantum computing": "quant-ph",
    "bioinformatics": "q-bio.GN",
    "robotics": "cs.RO",
    "computer science": "cs.AI",
}


def _parse_entry(entry) -> dict:
    """Parse a single Atom <entry> element into a paper dict."""
    def tag(name, ns="atom"):
        return entry.find(f"{ns}:{name}", NS)

    title_el = tag("title")
    summary_el = tag("summary")
    published_el = tag("published")
    id_el = tag("id")

    title = title_el.text.strip().replace("\n", " ") if title_el is not None else "Untitled"
    abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None else "No abstract available."
    pub_date = published_el.text.strip()[:10] if published_el is not None else ""
    year = pub_date[:4] if pub_date else "Unknown"
    arxiv_url = id_el.text.strip() if id_el is not None else ""

    # PDF link
    pdf_url = arxiv_url.replace("abs", "pdf") if arxiv_url else ""

    # Authors
    authors = []
    for author in entry.findall("atom:author", NS):
        name_el = author.find("atom:name", NS)
        if name_el is not None:
            authors.append(name_el.text.strip())
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    if not author_str:
        author_str = "Unknown Authors"

    # Categories
    categories = []
    for cat in entry.findall("atom:category", NS):
        term = cat.get("term", "")
        if term:
            categories.append(term)

    return {
        "id": arxiv_url,
        "title": title,
        "authors": author_str,
        "year": year,
        "published": pub_date,
        "citations": 0,  # arXiv doesn't provide citation counts
        "abstract": abstract,
        "url": arxiv_url,
        "pdf_url": pdf_url,
        "categories": categories,
        "source": "arXiv",
    }


async def search_papers(query: str, limit: int = 8) -> list:
    """
    Search arXiv using the Search API.
    Returns normalized paper dicts.
    """
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(ARXIV_SEARCH_URL, params=params)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            entries = root.findall("atom:entry", NS)
            return [_parse_entry(e) for e in entries]
    except Exception as e:
        print(f"arXiv search error: {e}")
        return []


async def fetch_category_feed(category_code: str, limit: int = 10) -> list:
    """
    Fetch latest papers from an arXiv RSS category feed.
    e.g. category_code = 'cs.AI'
    """
    url = f"{ARXIV_RSS_BASE}/{category_code}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Accept": "application/rss+xml"})
            resp.raise_for_status()

        # RSS feed uses a different namespace — parse manually
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []

        items = channel.findall("item")[:limit]
        papers = []
        for item in items:
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")

            title = title_el.text.strip() if title_el is not None else "Untitled"
            url = link_el.text.strip() if link_el is not None else ""

            # Strip HTML tags from description
            desc_raw = desc_el.text or "" if desc_el is not None else ""
            import re
            abstract = re.sub(r"<[^>]+>", "", desc_raw).strip()[:500]

            # Authors from dc:creator or title
            creator_el = item.find("{http://purl.org/dc/elements/1.1/}creator")
            authors = creator_el.text.strip() if creator_el is not None else "Unknown Authors"

            papers.append({
                "id": url,
                "title": title,
                "authors": authors,
                "year": str(datetime.now().year),
                "published": datetime.now().strftime("%Y-%m-%d"),
                "citations": 0,
                "abstract": abstract if abstract else "No abstract available.",
                "url": url,
                "pdf_url": url.replace("abs", "pdf") if "abs" in url else url,
                "categories": [category_code],
                "source": "arXiv",
            })
        return papers
    except Exception as e:
        print(f"arXiv RSS feed error ({category_code}): {e}")
        return []


async def fetch_multiple_feeds(category_codes: list, limit_per_feed: int = 5) -> list:
    """Fetch multiple RSS category feeds concurrently."""
    import asyncio
    results = await asyncio.gather(*[fetch_category_feed(c, limit_per_feed) for c in category_codes])
    papers = []
    for r in results:
        papers.extend(r)
    return papers
