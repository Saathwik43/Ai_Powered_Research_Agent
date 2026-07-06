import json
import logging
import time
import re

from ai.llm_provider import generate_completion

logger = logging.getLogger(__name__)

__all__ = ["extract_evidence"]

# In-memory evidence cache.
# Key: normalised_title_prefix  →  Value: (evidence_dict, timestamp)
# TTL: 600s (10 minutes)
_evidence_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 600

def _cache_key(paper: dict) -> str:
    """Stable cache key from first 60 chars of normalised title."""
    title = re.sub(r"[^a-z0-9 ]", "", (paper.get("title", "") or "").lower()).strip()[:60]
    return title

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

async def extract_evidence(paper: dict) -> dict:
    """
    Extract structured evidence fields from a paper's title/abstract using an LLM.
    Fail-open: If the LLM fails or returns invalid JSON, returns a dict with all empty fields.
    """
    default_empty = {
        "objective": "",
        "method": "",
        "dataset": "",
        "results": "",
        "limitations": "",
        "future_work": ""
    }
    
    title = paper.get("title", "")
    abstract = paper.get("abstract", "") or ""
    
    if not title and not abstract:
        return default_empty.copy()
        
    ck = _cache_key(paper)
    now = time.time()
    if ck in _evidence_cache:
        cached_evidence, cached_at = _evidence_cache[ck]
        if now - cached_at < _CACHE_TTL:
            return cached_evidence.copy()
        else:
            del _evidence_cache[ck]

    user_prompt = (
        f'Paper title: "{title}"\n'
        f'Paper abstract: "{abstract}"\n'
        'Output exactly the requested JSON.'
    )

    try:
        raw_response = await generate_completion(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=600,
            temperature=0.1,
            provider_override="groq"
        )
        
        content = raw_response.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found")
            
        parsed = json.loads(content[start:end])
        
        # Ensure all fields are present and are strings
        evidence = {}
        for key in default_empty:
            val = parsed.get(key, "")
            evidence[key] = str(val) if val is not None else ""
            
        _evidence_cache[ck] = (evidence.copy(), now)
        return evidence
        
    except Exception as e:
        logger.warning(f"Evidence extraction failed for '{title}', falling back to raw abstract. Error: {e}")
        return default_empty.copy()
