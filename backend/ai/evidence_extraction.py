from integrations.arxiv import get_arxiv_id, fetch_arxiv_html, fetch_latex_source
from integrations.europepmc import fetch_full_text, get_pmcid
import anyio.to_thread
import json
import logging
import re
import time
from ai.llm_provider import generate_completion
from ai.pdf_extraction import (
    _fetch_pdf_bytes,
    _match_alias,
    _append_text,
    _collapse_sections,
    _has_usable_evidence,
    _empty_evidence,
)
from ai.pdf_structure import extract_structure

logger = logging.getLogger(__name__)

__all__ = ["extract_evidence", "extract_evidence_for_paper", "_evidence_cache"]

EVIDENCE_FIELDS = (
    "objective",
    "method",
    "dataset",
    "results",
    "limitations",
    "future_work",
)


def empty_evidence() -> dict:
    return _empty_evidence()


# In-memory evidence cache.
# Key: normalised_title_prefix  ->  Value: (evidence_dict, timestamp, source)
# TTL: 600s (10 minutes)
_evidence_cache: dict[str, tuple[dict, float, str]] = {}
_CACHE_TTL = 600


def _cache_key(paper: dict) -> str:
    """Stable cache key from first 60 chars of normalised title."""
    title = re.sub(r"[^a-z0-9 ]", "", (paper.get("title", "") or "").lower()).strip()[:60]
    return title





def _get_cached_evidence(paper: dict) -> tuple[dict, str] | None:
    cache_key = _cache_key(paper)
    cached = _evidence_cache.get(cache_key)
    if not cached:
        return None

    cached_evidence, cached_at, cached_source = cached
    if time.time() - cached_at < _CACHE_TTL:
        return cached_evidence.copy(), cached_source

    del _evidence_cache[cache_key]
    return None


def _store_cached_evidence(paper: dict, evidence: dict, source: str) -> dict:
    _evidence_cache[_cache_key(paper)] = (evidence.copy(), time.time(), source)
    return evidence


_SYSTEM_PROMPT = """You are an academic extraction assistant. Extract factual evidence from the provided paper.
You must output ONLY valid JSON using exactly this schema:
{
  "objective": "",
  "method": "",
  "dataset": "",
  "results": "",
  "limitations": "",
  "future_work": ""
}
Extract ONLY what is explicitly stated in the provided abstract or title. Do NOT infer, guess, or synthesize information.
If a field is not addressed in the text, use an empty string ""."""


async def _extract_evidence_via_llm(paper: dict) -> dict:
    default_empty = empty_evidence()
    title = paper.get("title", "")
    abstract = paper.get("abstract", "") or ""

    if not title and not abstract:
        return default_empty

    user_prompt = (
        f'Paper title: "{title}"\n'
        f'Paper abstract: "{abstract}"\n'
        "Output exactly the requested JSON."
    )

    try:
        from ai.llm_provider import global_llm_sem
        async with global_llm_sem:
            raw_response = await generate_completion(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=600,
                temperature=0.1,
                provider_override="groq",
            )

        content = raw_response.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found")

        parsed = json.loads(content[start:end])

        evidence = {}
        for key in EVIDENCE_FIELDS:
            val = parsed.get(key, "")
            evidence[key] = str(val) if val is not None else ""
        return evidence

    except Exception as exc:
        logger.warning("Evidence extraction failed for '%s'; returning empty evidence. Error: %s", title, exc)
        return default_empty


async def extract_evidence(paper: dict) -> dict:
    """
    Legacy LLM-only evidence extraction entry point.
    Keeps the previous contract for existing callers and tests.
    """
    cached = _get_cached_evidence(paper)
    if cached:
        evidence, _source = cached
        return evidence

    evidence = await _extract_evidence_via_llm(paper)
    if _has_usable_evidence(evidence):
        _store_cached_evidence(paper, evidence, "llm-fallback")
    return evidence


def _safe_extract_structure(pdf_bytes: bytes) -> dict | None:
    """extract_structure raises on encrypted/corrupt PDFs; a bad download for
    one paper must not abort evidence extraction for the batch."""
    try:
        return extract_structure(pdf_bytes)
    except Exception as e:  # noqa: BLE001 - any parser failure is a soft miss
        logger.warning("PDF structure extraction failed: %s", e)
        return None


def _map_structure_to_evidence(structure_res: dict) -> dict:
    """Map a parsed structure dict (arXiv LaTeX or PDF) onto evidence fields."""
    sections: dict[str, list[str]] = {}

    abstract = structure_res.get("abstract", "").strip()
    if abstract:
        _append_text(sections, "objective", abstract)

    for key, text in structure_res.get("sections", {}).items():
        mapped = _match_alias(key)
        if mapped:
            _append_text(sections, mapped, text)
            
    evidence = _collapse_sections(sections)
    
    if not evidence["dataset"] and evidence["method"]:
        method_lower = evidence["method"].lower()
        if "dataset" in method_lower or "data" in method_lower or "corpus" in method_lower:
            evidence["dataset"] = evidence["method"]
            
    return evidence


async def extract_evidence_for_paper(paper: dict) -> tuple[dict, str]:
    """
    Layered extraction with cache and explicit source tracking.

    Order (cheapest and most structured first):
    1. Cache
    2. arXiv rendered HTML, then arXiv LaTeX source, for arXiv papers
    3. Europe PMC JATS full text, for open-access PMC records
    4. PDF structure extraction (PyMuPDF) when oa_url exists
    5. LLM title/abstract extraction
    """
    cached = _get_cached_evidence(paper)
    if cached:
        return cached

    title = paper.get("title", "Untitled Paper")

    def _accept(res, source):
        """Map a parsed {abstract, sections} result and cache it if usable."""
        if not res:
            return None
        evidence = _map_structure_to_evidence(res)
        if not _has_usable_evidence(evidence):
            return None
        _store_cached_evidence(paper, evidence, source)
        logger.info("Evidence extraction path for '%s': %s", title, source)
        return evidence

    arxiv_id = get_arxiv_id(paper)
    if arxiv_id:
        # arXiv's own LaTeXML rendering carries an explicit section tree, so it
        # beats both the LaTeX tarball (which needs macro-aware parsing) and the
        # PDF (whose structure is inferred from font sizes).
        evidence = _accept(await fetch_arxiv_html(arxiv_id), "arxiv-html")
        if evidence:
            return evidence, "arxiv-html"

        evidence = _accept(await fetch_latex_source(arxiv_id), "arxiv-latex")
        if evidence:
            return evidence, "arxiv-latex"

    pmcid = get_pmcid(paper)
    if pmcid:
        # Open-access PMC records expose JATS XML: a real section tree for
        # biomedical papers, keyless, and cheaper than fetching the PDF.
        evidence = _accept(await fetch_full_text(pmcid), "europepmc-fulltext")
        if evidence:
            return evidence, "europepmc-fulltext"

    oa_url = (paper.get("oa_url") or "").strip()

    if oa_url:
        pdf_bytes = await _fetch_pdf_bytes(oa_url)
        if pdf_bytes:
            # extract_structure is CPU-bound and synchronous; off-thread it so a
            # large PDF does not stall the event loop for every other request.
            # anyio, not asyncio.to_thread: the latter needs a running asyncio
            # loop and raises under the suite's trio backend.
            pdf_res = await anyio.to_thread.run_sync(_safe_extract_structure, pdf_bytes)
            if pdf_res:
                pdf_evidence = _map_structure_to_evidence(pdf_res)
                if _has_usable_evidence(pdf_evidence):
                    _store_cached_evidence(paper, pdf_evidence, "pdf-structure")
                    logger.info("Evidence extraction path for '%s': pdf-structure", title)
                    return pdf_evidence, "pdf-structure"

    llm_evidence = await _extract_evidence_via_llm(paper)
    if _has_usable_evidence(llm_evidence):
        _store_cached_evidence(paper, llm_evidence, "llm-fallback")
        logger.info("Evidence extraction path for '%s': llm-fallback", title)
        return llm_evidence, "llm-fallback"

    logger.info("Evidence extraction path for '%s': none", title)
    return empty_evidence(), "none"
