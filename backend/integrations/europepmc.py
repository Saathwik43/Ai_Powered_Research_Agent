import httpx
from integrations.http_client import pooled_client
import logging
import re
import xml.etree.ElementTree as ET
from services.api_telemetry import track_call

logger = logging.getLogger(__name__)

EUROPEPMC_REST_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EUROPEPMC_API_URL = f"{EUROPEPMC_REST_BASE}/search"

# Open-access articles in PMC expose their JATS XML here, keyless. That gives a
# real section tree for biomedical papers instead of inferring one from PDF
# layout, so it is tried before any PDF is downloaded.
_FULLTEXT_URL = EUROPEPMC_REST_BASE + "/{pmcid}/fullTextXML"


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
            async with pooled_client(timeout=15.0) as client:
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
                    "pmcid": pmcid,
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


def _jats_text(elem) -> str:
    """Flatten a JATS element to plain text, dropping nested markup."""
    if elem is None:
        return ""
    return " ".join(" ".join(elem.itertext()).split())


def _parse_jats(xml_text: str) -> dict | None:
    """Turn a JATS full-text document into the {abstract, sections} shape."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    abstract = _jats_text(root.find(".//front//abstract"))

    sections: dict[str, str] = {}
    body = root.find(".//body")
    for sec in (body.findall("sec") if body is not None else []):
        title_el = sec.find("title")
        heading = _jats_text(title_el).strip().lower()
        if not heading:
            continue
        # Drop the heading itself from the body text so it is not duplicated.
        if title_el is not None:
            sec.remove(title_el)
        text = _jats_text(sec)
        if text:
            sections[heading] = (sections[heading] + "\n" + text) if heading in sections else text

    if not sections and not abstract:
        return None
    return {"abstract": abstract, "sections": sections}


async def fetch_full_text(pmcid: str) -> dict | None:
    """Fetch open-access JATS full text for a PMC id.

    Returns None for records that are not open access (Europe PMC answers 404
    for those) so the caller can fall through to PDF extraction.
    """
    pmcid = (pmcid or "").strip().upper()
    if not pmcid.startswith("PMC"):
        return None

    async with track_call("Europe PMC", "fulltext") as rec:
        try:
            async with pooled_client(timeout=25.0) as client:
                resp = await client.get(_FULLTEXT_URL.format(pmcid=pmcid))
            if resp.status_code != 200:
                rec.fail(error=f"HTTP {resp.status_code}")
                return None
            parsed = _parse_jats(resp.text)
            rec.succeed(http_status=resp.status_code, items=1 if parsed else 0)
            return parsed
        except Exception as e:
            rec.fail(error=str(e))
            logger.warning("Europe PMC full text failed for %s: %s", pmcid, e)
            return None


_PMCID_RE = re.compile(r"\bPMC\d+\b", re.IGNORECASE)


def get_pmcid(paper: dict) -> str | None:
    """Find a PMC id on a paper dict, whichever source produced it."""
    direct = (paper.get("pmcid") or "").strip()
    if direct:
        return direct.upper()
    for field in ("url", "pdf_url", "oa_url", "id"):
        m = _PMCID_RE.search(str(paper.get(field) or ""))
        if m:
            return m.group(0).upper()
    return None
