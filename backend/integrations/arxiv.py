import httpx
from integrations.http_client import pooled_client
from core.ttl_cache import TTLCache
import xml.etree.ElementTree as ET
import logging
import os
from datetime import datetime
import re
import tarfile
import gzip
import html
import io

logger = logging.getLogger(__name__)

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

    arxiv_id = None
    if arxiv_url:
        m = re.search(r"arxiv\.org/abs/([\w.\-]+)", arxiv_url)
        arxiv_id = m.group(1) if m else None

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
        "doi": f"10.48550/arXiv.{arxiv_id}" if arxiv_id else "",
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
    email = os.getenv("CROSSREF_MAILTO", "")
    headers = {"User-Agent": f"AI-Powered-Research-Agent/1.0 (contact: {email})" if email else "AI-Powered-Research-Agent/1.0"}
    
    from services.api_telemetry import track_call
    async with track_call("arXiv", "search") as rec:
        try:
            async with pooled_client(headers=headers, timeout=15.0) as client:
                resp = await client.get(ARXIV_SEARCH_URL, params=params)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                entries = root.findall("atom:entry", NS)
                papers = [_parse_entry(e) for e in entries]
                rec.succeed(http_status=resp.status_code, items=len(papers))
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"arXiv search error: {e}")
            return []


_FEED_CACHE = TTLCache(maxsize=64, ttl=900)


async def fetch_category_feed(category_code: str, limit: int = 10) -> list:
    """
    Fetch latest papers from an arXiv category.
    e.g. category_code = 'cs.AI'

    Cached for 15 minutes. The Dashboard's left rail refetches on every click,
    and arXiv category listings change on the order of hours — not per click.
    """
    cache_key = f"{category_code}:{limit}"
    cached = _FEED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    params = {
        "search_query": f"cat:{category_code}",
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    email = os.getenv("CROSSREF_MAILTO", "")
    headers = {"User-Agent": f"AI-Powered-Research-Agent/1.0 (contact: {email})" if email else "AI-Powered-Research-Agent/1.0"}
    
    try:
        async with pooled_client(headers=headers, timeout=15.0) as client:
            resp = await client.get(ARXIV_SEARCH_URL, params=params)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            entries = root.findall("atom:entry", NS)
            papers = [_parse_entry(e) for e in entries]
            # Only cache a real result — an empty list from a transient error
            # would otherwise stick for the full TTL.
            if papers:
                _FEED_CACHE[cache_key] = papers
            return papers
    except Exception as e:
        logger.error(f"arXiv category feed error ({category_code}): {e}")
        return []


async def fetch_multiple_feeds(category_codes: list, limit_per_feed: int = 5) -> list:
    """Fetch multiple RSS category feeds concurrently."""
    import asyncio
    results = await asyncio.gather(*[fetch_category_feed(c, limit_per_feed) for c in category_codes])
    papers = []
    for r in results:
        papers.extend(r)
    return papers

def get_arxiv_id(paper: dict) -> str | None:
    """Extract the bare arXiv ID (e.g. '2301.12345') from a paper dict, if it's an arXiv paper."""
    url = (paper.get("url") or paper.get("arxiv_url") or paper.get("oa_url") or "").strip()
    m = re.search(r"arxiv\.org/(?:abs|pdf|e-print)/([\w.\-]+?)(?:v\d+)?(?:\.pdf)?$", url)
    return m.group(1) if m else None


_SECTION_RE = re.compile(r"\\section\*?\{([^}]*)\}")
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)


def _strip_latex_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


def _split_latex_sections(tex: str) -> dict:
    tex = _strip_latex_comments(tex)
    abstract_match = _ABSTRACT_RE.search(tex)
    abstract = abstract_match.group(1).strip() if abstract_match else ""

    sections = {}
    matches = list(_SECTION_RE.finditer(tex))
    for i, m in enumerate(matches):
        heading = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(tex)
        body = tex[start:end].strip()
        if heading and body:
            sections[heading] = body

    return {"abstract": abstract, "sections": sections}


async def fetch_latex_source(arxiv_id: str) -> dict | None:
    """Fetch and lightly parse arXiv LaTeX source. Returns None if unavailable
    (old scanned papers, PDF-only submissions) or on any failure — caller falls
    back to PDF structure extraction in that case."""
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    try:
        async with pooled_client(timeout=20.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            raw = resp.content
    except Exception as e:
        logger.warning(f"arXiv e-print fetch failed for {arxiv_id}: {e}")
        return None

    try:
        # e-print is usually a gzipped tarball of multiple .tex files
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            tex_parts = []
            for member in tar.getmembers():
                if member.name.endswith(".tex"):
                    f = tar.extractfile(member)
                    if f:
                        tex_parts.append(f.read().decode("utf-8", errors="ignore"))
            full_tex = "\n".join(tex_parts)
    except tarfile.ReadError:
        # Sometimes it's a single gzipped .tex file, not a tarball
        try:
            full_tex = gzip.decompress(raw).decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"arXiv source for {arxiv_id} not a tar or gzip: {e}")
            return None
    except Exception as e:
        logger.warning(f"arXiv source extraction failed for {arxiv_id}: {e}")
        return None

    if not full_tex.strip():
        return None

    return _split_latex_sections(full_tex)

# arXiv renders LaTeXML HTML for submissions from ~Dec 2023 at /html/{id};
# ar5iv backfills older papers. Both emit the same `ltx_*` class vocabulary, so
# one parser covers both. This is strictly better than parsing the PDF: the
# section tree is explicit rather than inferred from font sizes.
_ARXIV_HTML_URL = "https://arxiv.org/html/{id}"
_AR5IV_HTML_URL = "https://ar5iv.labs.arxiv.org/html/{id}"

_LTX_HEAD_RE = re.compile(
    r'<h[1-6][^>]*\bltx_title_section\b[^>]*>(.*?)</h[1-6]>', re.DOTALL | re.IGNORECASE
)
_LTX_TAG_RE = re.compile(r'<span[^>]*\bltx_tag\b[^>]*>.*?</span>', re.DOTALL | re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r'<(script|style)\b.*?</\1>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_ABSTRACT_BLOCK_RE = re.compile(r'\bltx_abstract\b', re.IGNORECASE)


def _html_text(fragment: str) -> str:
    """Strip markup and normalise whitespace/entities from an HTML fragment."""
    text = _TAG_RE.sub(" ", fragment)
    return " ".join(html.unescape(text).split())


def _parse_arxiv_html(page: str) -> dict | None:
    """Parse LaTeXML output into the {abstract, sections} shape."""
    page = _SCRIPT_STYLE_RE.sub(" ", page)

    heads = list(_LTX_HEAD_RE.finditer(page))
    if not heads:
        return None

    abstract = ""
    marker = _ABSTRACT_BLOCK_RE.search(page)
    if marker and marker.start() < heads[0].start():
        raw = _html_text(page[marker.end():heads[0].start()])
        # The block opens with its own "Abstract" heading; drop that word.
        abstract = re.sub(r'^["\'>\s]*Abstract\b[:\s]*', "", raw, flags=re.IGNORECASE).strip()

    sections: dict[str, str] = {}
    for i, m in enumerate(heads):
        heading = _html_text(_LTX_TAG_RE.sub(" ", m.group(1))).strip().lower()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(page)
        body = _html_text(page[m.end():end])
        if heading and body:
            sections[heading] = (sections[heading] + "\n" + body) if heading in sections else body

    if not sections and not abstract:
        return None
    return {"abstract": abstract, "sections": sections}


async def fetch_arxiv_html(arxiv_id: str) -> dict | None:
    """Fetch arXiv's rendered HTML (ar5iv for older papers) and parse its
    sections. Returns None when neither host has an HTML rendering, so the
    caller falls through to the LaTeX source and then to PDF parsing."""
    for template in (_ARXIV_HTML_URL, _AR5IV_HTML_URL):
        url = template.format(id=arxiv_id)
        try:
            async with pooled_client(timeout=25.0) as client:
                resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                continue
            parsed = _parse_arxiv_html(resp.text)
            if parsed:
                return parsed
        except Exception as e:
            logger.warning("arXiv HTML fetch failed for %s at %s: %s", arxiv_id, url, e)
    return None


_ARXIV_ID_IN_TEXT_RE = re.compile(r"arXiv:\s*([\w.\-]+)(?:v\d+)?", re.IGNORECASE)

def detect_arxiv_id_from_text(first_page_text: str) -> str | None:
    """arXiv papers self-print their ID (e.g. 'arXiv:2301.12345v2') on page 1 —
    detect it from raw extracted text of an uploaded PDF."""
    m = _ARXIV_ID_IN_TEXT_RE.search(first_page_text or "")
    return m.group(1) if m else None