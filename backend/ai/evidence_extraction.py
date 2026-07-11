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
from ai.grobid_client import extract_via_grobid

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


def _map_grobid_to_evidence(grobid_res: dict) -> dict:
    sections: dict[str, list[str]] = {}
    
    abstract = grobid_res.get("abstract", "").strip()
    if abstract:
        _append_text(sections, "objective", abstract)
        
    for key, text in grobid_res.get("sections", {}).items():
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

    Order:
    1. Cache
    2. PDF extraction via grobid_client when oa_url exists
    3. Existing LLM title/abstract extraction
    """
    cached = _get_cached_evidence(paper)
    if cached:
        return cached

    title = paper.get("title", "Untitled Paper")
    oa_url = (paper.get("oa_url") or "").strip()

    if oa_url:
        pdf_bytes = await _fetch_pdf_bytes(oa_url)
        if pdf_bytes:
            grobid_res = await extract_via_grobid(pdf_bytes)
            if grobid_res:
                pdf_evidence = _map_grobid_to_evidence(grobid_res)
                if _has_usable_evidence(pdf_evidence):
                    _store_cached_evidence(paper, pdf_evidence, "grobid")
                    logger.info("Evidence extraction path for '%s': grobid", title)
                    return pdf_evidence, "grobid"

    llm_evidence = await _extract_evidence_via_llm(paper)
    if _has_usable_evidence(llm_evidence):
        _store_cached_evidence(paper, llm_evidence, "llm-fallback")
        logger.info("Evidence extraction path for '%s': llm-fallback", title)
        return llm_evidence, "llm-fallback"

    logger.info("Evidence extraction path for '%s': none", title)
    return empty_evidence(), "none"
