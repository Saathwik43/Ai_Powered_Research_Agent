"""
GROBID client -- hosted, keyless, free-tier HF Space mirror.
Primary structure-extraction tier: calls GROBID's header + fulltext endpoints,
parses TEI XML, returns the same shape as pdf_structure.extract_structure()
so pdf_analysis.py can swap tiers transparently.
"""
import logging
import re
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_GROBID_BASE_URL = "https://lfoppiano-grobid.hf.space"
_TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
_TIMEOUT = 20.0

# section-key hygiene: reject figure-caption-style headings if GROBID ever
# mis-segments (rare, but keep the same guard the heuristic tier uses)
_CAPTION_LABEL_RE = re.compile(r'^\(?[a-hA-H]\)?(\s*\(?[a-hA-H]\)?)*$')


def _text_of(elem) -> str:
    """Join all text content of an element and its children, collapsing whitespace."""
    if elem is None:
        return ""
    parts = list(elem.itertext())
    return " ".join(" ".join(parts).split())


async def _post_pdf(client, endpoint, file_bytes):
    """POST PDF bytes to a GROBID endpoint, return TEI XML text or None on failure.
    Retries once on timeout/5xx (HF Space free tier can cold-start slow)."""
    url = _GROBID_BASE_URL + endpoint
    for attempt in range(2):
        try:
            resp = await client.post(
                url,
                files={"input": ("paper.pdf", file_bytes, "application/pdf")},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
            logger.warning("GROBID %s returned %s on attempt %s", endpoint, resp.status_code, attempt + 1)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            logger.warning("GROBID %s failed on attempt %s: %s", endpoint, attempt + 1, e)
    return None


def _parse_header(tei_xml):
    """Parse processHeaderDocument TEI -> (title, authors, abstract)."""
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError:
        return "", [], ""

    title = ""
    title_elem = root.find(".//tei:titleStmt/tei:title", _TEI_NS)
    if title_elem is not None:
        title = _text_of(title_elem)

    authors = []
    for pers in root.findall(".//tei:sourceDesc//tei:analytic/tei:author/tei:persName", _TEI_NS):
        forename = pers.find("tei:forename", _TEI_NS)
        surname = pers.find("tei:surname", _TEI_NS)
        parts = []
        if forename is not None and forename.text:
            parts.append(forename.text.strip())
        if surname is not None and surname.text:
            parts.append(surname.text.strip())
        name = " ".join(parts).strip()
        if name:
            authors.append(name)

    abstract = ""
    abstract_elem = root.find(".//tei:profileDesc/tei:abstract", _TEI_NS)
    if abstract_elem is not None:
        paras = abstract_elem.findall(".//tei:p", _TEI_NS)
        if paras:
            abstract = " ".join(_text_of(p) for p in paras).strip()
        else:
            abstract = _text_of(abstract_elem)

    return title, authors, abstract


def _parse_fulltext_sections(tei_xml):
    """Parse processFulltextDocument TEI -> ({section_key: section_text}, headless_intro).
    Each <div> under <text><body> with a <head> is one section; div content
    (all <p> descendants) becomes the section body. The first <div> without a <head>
    is captured as headless_intro (often the abstract for PRL-style papers)."""
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError:
        return {}, ""

    sections = {}
    headless_intro = ""
    divs = root.findall(".//tei:text/tei:body/tei:div", _TEI_NS)
    for div in divs:
        head = div.find("tei:head", _TEI_NS)
        head_text = _text_of(head) if head is not None else ""
        if not head_text:
            if not sections and not headless_intro:
                paras = div.findall("tei:p", _TEI_NS)
                abs_paras = []
                intro_paras = []
                word_count = 0
                for p in paras:
                    ptxt = _text_of(p).strip()
                    if not ptxt:
                        continue
                    if word_count < 250:
                        abs_paras.append(ptxt)
                        word_count += len(ptxt.split())
                    else:
                        intro_paras.append(ptxt)
                
                headless_intro = "\n".join(abs_paras).strip()
                if intro_paras:
                    sections["introduction"] = "\n".join(intro_paras).strip()
            continue

        # guard against figure-caption-style headings slipping through and citation fragments
        lower_head = head_text.lower()
        if _CAPTION_LABEL_RE.match(head_text.strip()) or len(head_text) < 3 or "et al" in lower_head:
            continue

        paras = div.findall("tei:p", _TEI_NS)
        body_text = "\n".join(_text_of(p) for p in paras if _text_of(p)).strip()
        if not body_text:
            continue

        key = head_text.lower()
        if "introduction" in key:
            key = "introduction"
        elif "material" in key:
            key = "materials"
        elif "method" in key:
            key = "method"
        elif "result" in key and "discussion" in key:
            key = "results_and_discussion"
        elif "result" in key:
            key = "results"
        elif "discussion" in key:
            key = "discussion"
        elif "conclusion" in key:
            key = "conclusion"
        elif "acknowledg" in key:
            key = "acknowledgments"
        elif "reference" in key or "bibliograph" in key:
            key = "references"

        # merge if key repeats (e.g. multiple sub-divs under same heading)
        if key in sections:
            sections[key] = sections[key] + "\n" + body_text
        else:
            sections[key] = body_text

    # Extract from back matter (references, acknowledgments)
    back_divs = root.findall(".//tei:text/tei:back/tei:div", _TEI_NS)
    for div in back_divs:
        div_type = div.get("type", "")
        if div_type == "references":
            bibls = div.findall(".//tei:listBibl/tei:biblStruct", _TEI_NS)
            if bibls:
                sections["references"] = "\n\n".join(_text_of(b) for b in bibls).strip()
            else:
                sections["references"] = _text_of(div).strip()
        elif div_type == "acknowledgement":
            sections["acknowledgments"] = _text_of(div).strip()

    return sections, headless_intro


def _score_confidence(title, authors, abstract, sections):
    return {
        "title": "high" if len(title) >= 8 else "low",
        "authors": "high" if 1 <= len(authors) <= 15 else "low",
        "abstract": "high" if len(abstract.split()) >= 40 else "low",
        "sections": "high" if len(sections) >= 3 else "low",
    }


async def extract_via_grobid(file_bytes: bytes):
    """
    Primary structure-extraction tier. Returns None if GROBID is unreachable
    entirely (both endpoints fail after retry) so the caller can fall back to
    pdf_structure.extract_structure(). Returns the same shape as that
    function otherwise, so pdf_analysis.py can swap tiers transparently.
    """
    async with httpx.AsyncClient() as client:
        header_xml, fulltext_xml = None, None
        try:
            header_xml = await _post_pdf(client, "/api/processHeaderDocument", file_bytes)
            fulltext_xml = await _post_pdf(client, "/api/processFulltextDocument", file_bytes)
        except Exception as e:
            logger.warning("GROBID extraction failed entirely: %s", e)

    if not header_xml and not fulltext_xml:
        return None

    title, authors, abstract = _parse_header(header_xml) if header_xml else ("", [], "")
    sections, headless_intro = _parse_fulltext_sections(fulltext_xml) if fulltext_xml else ({}, "")

    if not abstract and headless_intro:
        abstract = headless_intro

    if not sections:
        sections = {"full_text": ""}

    confidence = _score_confidence(title, authors, abstract, sections)

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "sections": sections,
        "confidence": confidence,
    }
