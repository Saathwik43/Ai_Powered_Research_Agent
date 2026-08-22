import logging
import re
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

# Headings seen in the wild across the three structured tiers (arXiv LaTeXML,
# Europe PMC JATS, PDF layout). ML papers in particular label their methods
# section "Model Architecture" or "Approach" rather than "Methods", which the
# original table missed -- the Transformer paper mapped no `method` evidence at
# all until those aliases were added.
SECTION_ALIASES = {
    "objective": ("abstract", "introduction", "objective", "objectives", "aim", "aims", "background"),
    "method": (
        "method", "methods", "materials and methods", "methodology", "approach",
        "our approach", "proposed method", "proposed approach", "experimental setup",
        "materials", "model", "models", "model architecture", "architecture",
        "training", "study design", "experimental design", "implementation",
    ),
    "dataset": (
        "dataset", "datasets", "data", "data set", "corpus", "benchmarks", "benchmark",
        "participants", "data collection", "training data",
    ),
    "results": (
        "results", "findings", "evaluation", "experiments", "experimental results",
        "experiments and results", "empirical results", "analysis",
        "results_and_discussion", "results and discussion", "discussion", "performance",
    ),
    "limitations": ("limitations", "limitation", "threats to validity", "constraints"),
    "future_work": (
        "future work", "future_work", "future directions", "conclusion", "conclusions",
        "conclusion and future work", "outlook", "next steps",
    ),
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


_HEADING_NUMBER_PREFIX_RE = re.compile(r'^\s*(?:\d+(?:\.\d+)*|[ivxlc]+|[a-z])[.)]?\s+')


def _match_alias(name: str) -> str | None:
    normalized = _normalize_text(name).lower()
    if not normalized:
        return None
    # Sources differ on whether the heading carries its number ("2 Background"
    # from a PDF vs "Background" from JATS). Match on the bare heading so one
    # alias table serves every tier.
    candidates = [normalized]
    stripped = _HEADING_NUMBER_PREFIX_RE.sub("", normalized).strip()
    if stripped and stripped != normalized:
        candidates.append(stripped)

    for target, aliases in SECTION_ALIASES.items():
        if any(c in aliases for c in candidates):
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



