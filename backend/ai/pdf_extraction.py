import logging
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

EVIDENCE_FIELDS = (
    "objective",
    "method",
    "dataset",
    "results",
    "limitations",
    "future_work",
)

SECTION_ALIASES = {
    "objective": ("abstract", "introduction", "objective", "objectives", "aim", "aims", "background"),
    "method": ("method", "methods", "materials and methods", "methodology", "approach", "experimental setup"),
    "dataset": ("dataset", "data", "data set", "corpus", "benchmarks", "benchmark", "participants"),
    "results": ("results", "findings", "evaluation", "experiments", "experimental results", "analysis"),
    "limitations": ("limitations", "limitation", "threats to validity", "constraints", "discussion"),
    "future_work": ("future work", "conclusion", "conclusions", "outlook", "next steps"),
}

GROBID_URL = os.getenv("GROBID_URL", "http://localhost:8070").rstrip("/")
_FETCH_TIMEOUT_SECONDS = 5.0
_GROBID_TIMEOUT_SECONDS = 8.0
_GROBID_HEALTH_TIMEOUT_SECONDS = 1.0
_GROBID_DOWN_TTL_SECONDS = 60.0
_GROBID_HEALTH_PATH = "/api/isalive"

_grobid_state = {"available": None, "checked_at": 0.0}

__all__ = ["extract_evidence_from_pdf"]


def _empty_evidence() -> dict:
    return {field: "" for field in EVIDENCE_FIELDS}


def _has_usable_evidence(evidence: dict | None) -> bool:
    return bool(evidence and any((evidence.get(field) or "").strip() for field in EVIDENCE_FIELDS))


def _is_high_confidence(evidence: dict | None) -> bool:
    if not _has_usable_evidence(evidence):
        return False
    populated = sum(1 for field in EVIDENCE_FIELDS if (evidence.get(field) or "").strip())
    return populated >= 2 and bool((evidence.get("method") or "").strip()) and bool((evidence.get("results") or "").strip())


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _match_alias(name: str) -> str | None:
    normalized = _normalize_text(name).lower()
    if not normalized:
        return None
    for target, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return target
    return None


def _append_text(bucket: dict[str, list[str]], field: str, value: str | None) -> None:
    text = _normalize_text(value)
    if text:
        bucket.setdefault(field, []).append(text)


def _collapse_sections(bucket: dict[str, list[str]]) -> dict:
    evidence = _empty_evidence()
    for field in EVIDENCE_FIELDS:
        if bucket.get(field):
            evidence[field] = " ".join(dict.fromkeys(bucket[field]))
    return evidence


async def _fetch_pdf_bytes(pdf_url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(pdf_url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" not in content_type and not pdf_url.lower().endswith(".pdf"):
                logger.info("Skipping non-PDF OA URL: %s", pdf_url)
                return None
            return response.content
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
        logger.info("Failed to fetch PDF from %s: %s", pdf_url, exc)
        return None


def _flatten_docling_node(node, *, current_section: str | None = None, sections: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    if sections is None:
        sections = {}

    if node is None:
        return sections

    if isinstance(node, dict):
        next_section = current_section

        for section_key in ("section", "section_name", "section_title", "title", "name", "label", "header", "head"):
            mapped = _match_alias(node.get(section_key, ""))
            if mapped:
                next_section = mapped
                break

        if next_section:
            for text_key in ("text", "content", "value", "orig", "orig_text", "raw_text"):
                _append_text(sections, next_section, node.get(text_key))

        for value in node.values():
            _flatten_docling_node(value, current_section=next_section, sections=sections)
        return sections

    if isinstance(node, (list, tuple, set)):
        for item in node:
            _flatten_docling_node(item, current_section=current_section, sections=sections)
        return sections

    if isinstance(node, str) and current_section:
        _append_text(sections, current_section, node)
        return sections

    if hasattr(node, "model_dump"):
        return _flatten_docling_node(node.model_dump(), current_section=current_section, sections=sections)

    if hasattr(node, "export_to_dict"):
        return _flatten_docling_node(node.export_to_dict(), current_section=current_section, sections=sections)

    if hasattr(node, "__dict__"):
        return _flatten_docling_node(vars(node), current_section=current_section, sections=sections)

    return sections


def _map_docling_document_to_evidence(document) -> dict:
    sections = _flatten_docling_node(document)
    evidence = _collapse_sections(sections)

    if not _has_usable_evidence(evidence) and hasattr(document, "export_to_markdown"):
        sections = {}
        current_section = None
        try:
            markdown = document.export_to_markdown() or ""
        except Exception:
            markdown = ""

        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                current_section = _match_alias(line.lstrip("#").strip())
                continue
            if current_section:
                _append_text(sections, current_section, line)

        evidence = _collapse_sections(sections)

    if not evidence["dataset"] and evidence["method"]:
        method_lower = evidence["method"].lower()
        if "dataset" in method_lower or "data" in method_lower or "corpus" in method_lower:
            evidence["dataset"] = evidence["method"]

    return evidence


def _convert_pdf_with_docling(pdf_bytes: bytes):
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(pdf_bytes)
        temp_path = Path(handle.name)

    try:
        result = converter.convert(str(temp_path))
        return getattr(result, "document", result)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to delete temporary Docling PDF: %s", temp_path)


def _extract_with_docling(pdf_bytes: bytes) -> dict | None:
    try:
        document = _convert_pdf_with_docling(pdf_bytes)
        evidence = _map_docling_document_to_evidence(document)
        return evidence if _has_usable_evidence(evidence) else None
    except ImportError:
        logger.warning("Docling is not installed; skipping Docling PDF extraction.")
        return None
    except Exception as exc:
        logger.info("Docling PDF extraction failed: %s", exc)
        return None


async def _check_grobid_health() -> bool:
    now = time.time()
    cached_state = _grobid_state["available"]
    checked_at = _grobid_state["checked_at"]
    if cached_state is False and now - checked_at < _GROBID_DOWN_TTL_SECONDS:
        return False
    if cached_state is True and now - checked_at < _GROBID_DOWN_TTL_SECONDS:
        return True

    try:
        async with httpx.AsyncClient(timeout=_GROBID_HEALTH_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{GROBID_URL}{_GROBID_HEALTH_PATH}")
            healthy = response.is_success and "true" in response.text.lower()
            _grobid_state["available"] = healthy
            _grobid_state["checked_at"] = now
            return healthy
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        logger.info("GROBID health check failed fast: %s", exc)
    except httpx.RequestError as exc:
        logger.info("GROBID health check request error: %s", exc)

    _grobid_state["available"] = False
    _grobid_state["checked_at"] = now
    return False


def _mark_grobid_unavailable() -> None:
    _grobid_state["available"] = False
    _grobid_state["checked_at"] = time.time()


def _extract_plaintext(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return _normalize_text(" ".join(text for text in element.itertext()))


def _tei_root(xml_text: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.info("Failed to parse GROBID TEI XML: %s", exc)
        return None


def _map_tei_to_evidence(xml_text: str) -> dict | None:
    root = _tei_root(xml_text)
    if root is None:
        return None

    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    sections: dict[str, list[str]] = {}

    abstract = root.find(".//tei:profileDesc/tei:abstract", ns)
    _append_text(sections, "objective", _extract_plaintext(abstract))

    for div in root.findall(".//tei:text/tei:body//tei:div", ns):
        head = _extract_plaintext(div.find("tei:head", ns))
        mapped = _match_alias(head)
        if not mapped:
            continue
        paragraphs = [_extract_plaintext(paragraph) for paragraph in div.findall("tei:p", ns)]
        _append_text(sections, mapped, " ".join(p for p in paragraphs if p))

    evidence = _collapse_sections(sections)
    return evidence if _has_usable_evidence(evidence) else None


async def _extract_with_grobid(pdf_bytes: bytes) -> dict | None:
    if not await _check_grobid_health():
        return None

    files = {"input": ("paper.pdf", pdf_bytes, "application/pdf")}
    data = {"consolidateHeader": "0", "consolidateCitations": "0"}

    try:
        async with httpx.AsyncClient(timeout=_GROBID_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{GROBID_URL}/api/processFulltextDocument",
                files=files,
                data=data,
            )
            response.raise_for_status()
            return _map_tei_to_evidence(response.text)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        logger.info("GROBID is unavailable; skipping remaining GROBID attempts for now: %s", exc)
        _mark_grobid_unavailable()
        return None
    except httpx.TimeoutException as exc:
        logger.info("GROBID extraction timed out: %s", exc)
        return None
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        logger.info("GROBID extraction failed: %s", exc)
        return None


async def extract_evidence_from_pdf_with_source(pdf_url: str) -> tuple[dict | None, str | None]:
    pdf_bytes = await _fetch_pdf_bytes(pdf_url)
    if not pdf_bytes:
        return None, None

    docling_evidence = _extract_with_docling(pdf_bytes)
    if _is_high_confidence(docling_evidence):
        return docling_evidence, "docling"

    grobid_evidence = await _extract_with_grobid(pdf_bytes)
    if _has_usable_evidence(grobid_evidence):
        return grobid_evidence, "grobid"

    if _has_usable_evidence(docling_evidence):
        return docling_evidence, "docling"

    return None, None


async def extract_evidence_from_pdf(pdf_url: str) -> dict | None:
    evidence, _source = await extract_evidence_from_pdf_with_source(pdf_url)
    return evidence
