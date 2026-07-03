"""
ai/gap_analysis.py
------------------
Structured gap analysis for a research topic — Stage 2 of the pipeline.

Given a set of retrieved and relevance-filtered papers, asks Gemini Pro to
produce a structured synthesis: what is well-covered, what gaps remain, and
one concrete research direction.

Includes:
  - topic_unclear escape hatch (backstop for borderline-nonsense topics)
  - Vagueness heuristic for suggested_direction
  - Retry logic on vagueness rejection
"""

import json
import logging
import re

from ai.llm_provider import generate_completion
from ai.guardrails import validate_input_layers_a_b
from ai.relevance import _filter_relevant_papers
from integrations.paper_search import search_all
from fastapi import HTTPException

logger = logging.getLogger(__name__)

__all__ = ["analyze_gaps"]

# Generic phrases that indicate a vague suggested_direction.
_VAGUE_PHRASES = [
    "further research",
    "more research is needed",
    "additional studies",
    "warrants investigation",
    "needs further exploration",
    "remains to be seen",
    "future work should",
    "more work is needed",
]


def _is_vague(direction: str) -> bool:
    """Return True if the suggested_direction is too short or generic."""
    if not direction or len(direction.split()) < 20:
        return True
    direction_lower = direction.lower()
    return any(phrase in direction_lower for phrase in _VAGUE_PHRASES)


_GAP_SYSTEM_PROMPT = "You are an expert research strategist. You analyze academic literature to identify gaps and opportunities."

_GAP_USER_TEMPLATE = """You are analyzing the current state of research on the topic: "{topic}"

CRITICAL INSTRUCTION: If the topic "{topic}" is complete gibberish, a random string of characters, a nonsensical combination of unrelated everyday words, or doesn't correspond to a coherent, recognizable academic research subject, you MUST immediately output EXACTLY the following JSON and nothing else:
{{"error": "topic_unclear"}}

Below is a numbered reference list of recent papers on this topic:
{ref_text}

Based on ONLY the papers listed above, produce a JSON object with exactly three fields:

1. "well_covered": an array of short strings (one sentence each) summarizing what aspects of this topic are already well-established in the literature. Cite relevant paper numbers, e.g. "Spontaneous polarization in NF phases is well-characterized [1],[3]."

2. "gaps": an array of short strings identifying specific under-explored areas or contradictions. Each gap MUST reference which numbered papers informed the identification of that gap, e.g. "Electroviscous coupling under AC fields remains uncharacterized despite [2],[5] studying DC-field effects."

3. "suggested_direction": ONE concrete, specific, actionable research direction that addresses the most significant gap. This must be a detailed proposal (at least 20 words) — NOT generic filler like "more research is needed" or "further studies should be conducted." Include specific methodology, variables, or phenomena to investigate.

Output ONLY valid JSON with these three fields. No markdown, no explanation, no preamble."""


async def analyze_gaps(topic: str) -> dict:
    """
    Run structured gap analysis for *topic*.

    1. Retrieves papers via search_all + relevance filter (reusing existing infra).
    2. If < 2 papers pass filter, returns insufficient_literature.
    3. Prompts Gemini Pro for structured gap/opportunity JSON.
    4. Applies vagueness heuristic; retries once on failure.

    Returns
    -------
    dict
        Keys: well_covered, gaps, suggested_direction, references
        OR: status='insufficient_literature', paper_count=N
        OR raises HTTPException for guardrail failures.
    """
    # Layer A/B guardrails
    if not validate_input_layers_a_b(topic):
        raise HTTPException(status_code=400, detail="The provided topic is unclear or appears to be nonsense.")

    # Retrieve and filter
    papers = await search_all(topic, limit=8) or []
    if papers:
        papers = await _filter_relevant_papers(topic, papers)

    if len(papers) < 2:
        return {
            "status": "insufficient_literature",
            "paper_count": len(papers),
            "message": f"Only {len(papers)} relevant paper(s) found for '{topic}'. "
                       "Gap analysis requires at least 2 papers for meaningful synthesis.",
        }

    # Build numbered reference list (same format as manuscript_generation.py)
    ref_text = ""
    references_mapping = {}
    for idx, p in enumerate(papers, 1):
        title = p.get("title", "Unknown Title")
        authors = p.get("authors", "Unknown Authors")
        year = p.get("year", "Unknown Year")
        abstract = (p.get("abstract", "") or "")[:300]
        doi = p.get("doi", p.get("url", ""))
        ref_text += f"[{idx}] {authors} ({year}). {title}. {abstract}. {doi}\n"
        references_mapping[str(idx)] = p

    user_prompt = _GAP_USER_TEMPLATE.format(topic=topic, ref_text=ref_text)

    # Try up to 2 times (initial + retry on vagueness)
    last_result = None
    for attempt in range(2):
        prompt = user_prompt
        if attempt == 1:
            # Stronger prompt on retry
            prompt += (
                "\n\nIMPORTANT: Your previous response was rejected because the suggested_direction "
                "was too vague or generic. This time, provide a HIGHLY SPECIFIC research direction "
                "including exact methodology, specific variables or materials, and measurable outcomes. "
                "Do NOT use phrases like 'more research is needed' or 'further studies should be conducted'."
            )

        try:
            raw = await generate_completion(
                system_prompt=_GAP_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=1200,
                temperature=0.3,
                provider_override="gemini",
            )

            # Check for topic_unclear escape hatch
            if '{"error": "topic_unclear"}' in raw or '"error"' in raw and "topic_unclear" in raw:
                raise HTTPException(
                    status_code=400,
                    detail="The provided topic is unclear or appears to be nonsense.",
                )

            # Parse JSON from response
            content = raw.strip()
            # Find JSON object boundaries
            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON object found in response")

            parsed = json.loads(content[start:end])

            # Validate required fields
            well_covered = parsed.get("well_covered", [])
            gaps = parsed.get("gaps", [])
            suggested_direction = parsed.get("suggested_direction", "")

            if not isinstance(well_covered, list) or not isinstance(gaps, list):
                raise ValueError("well_covered and gaps must be arrays")

            last_result = {
                "well_covered": well_covered,
                "gaps": gaps,
                "suggested_direction": suggested_direction,
                "references": references_mapping,
            }

            # Vagueness check
            if _is_vague(suggested_direction):
                if attempt == 0:
                    logger.info(f"Gap analysis suggested_direction too vague, retrying: {suggested_direction[:80]}")
                    continue
                else:
                    # Second attempt still vague — return with warning flag
                    last_result["vagueness_warning"] = True
                    logger.warning(f"Gap analysis suggested_direction still vague after retry: {suggested_direction[:80]}")

            return last_result

        except HTTPException:
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Gap analysis JSON parse error (attempt {attempt+1}): {e}")
            if attempt == 1:
                raise HTTPException(status_code=502, detail="Gap analysis produced invalid JSON.")
        except Exception as e:
            logger.error(f"Gap analysis failed (attempt {attempt+1}): {e}")
            if attempt == 1:
                raise HTTPException(status_code=503, detail="Gap analysis unavailable.")

    # Should not reach here, but safety net
    if last_result:
        last_result["vagueness_warning"] = True
        return last_result
    raise HTTPException(status_code=503, detail="Gap analysis unavailable.")
