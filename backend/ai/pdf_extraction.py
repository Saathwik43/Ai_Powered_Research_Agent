import logging
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
    "method": ("method", "methods", "materials and methods", "methodology", "approach", "experimental setup", "materials"),
    "dataset": ("dataset", "data", "data set", "corpus", "benchmarks", "benchmark", "participants"),
    "results": ("results", "findings", "evaluation", "experiments", "experimental results", "analysis", "results_and_discussion", "discussion"),
    "limitations": ("limitations", "limitation", "threats to validity", "constraints"),
    "future_work": ("future work", "conclusion", "conclusions", "outlook", "next steps"),
}

IGNORED_SECTIONS = {"acknowledgments", "acknowledgements", "references", "appendix"}

_FETCH_TIMEOUT_SECONDS = 5.0

__all__ = [
    "EVIDENCE_FIELDS",
    "SECTION_ALIASES",
    "_fetch_pdf_bytes",
    "_match_alias",
    "_append_text",
    "_collapse_sections",
    "_has_usable_evidence",
    "_empty_evidence",
]


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
    if normalized in IGNORED_SECTIONS:
        logger.debug("Explicitly ignoring section: %s", name)
    else:
        logger.info("Unrecognized section silently dropped: %s", name)
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



