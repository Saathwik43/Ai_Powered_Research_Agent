import logging
from fastapi import HTTPException
from ai.guardrails import validate_input_layers_a_b
from ai.keyword_extractor import extract_top_topics
from integrations.paper_search import search_all

logger = logging.getLogger(__name__)


def _fallback_topics(intent: str):
    return [
        {"id": 1, "title": f"Advancements in {intent}", "impact": "High"},
        {"id": 2, "title": f"Emerging Applications of {intent}", "impact": "High"},
        {"id": 3, "title": f"Challenges and Future Directions in {intent}", "impact": "Medium"},
    ]


async def discover_topics(intent: str):
    """
    Discover trending research topics by aggregating papers from all
    configured sources (OpenAlex, Semantic Scholar, arXiv, Crossref,
    PubMed, Springer, IEEE, CORE, GitHub) and extracting the most
    frequent keyword phrases — no LLM required.
    """
    # ── Guardrail check (unchanged) ──────────────────────────────────
    if not validate_input_layers_a_b(intent):
        return {"data": [], "source": "aggregated", "coherence_check": "failed"}

    try:
        # ── 1. Fetch papers from ALL sources (fast, AI-free) ─────────
        papers = await search_all(
            intent,
            limit_per_source=3,       # small batch per source keeps it fast
            semantic_rerank=False,     # skip the AI-based reranking step
        )

        if not papers:
            logger.warning(f"No papers found for intent '{intent}', using fallback topics.")
            return {"data": _fallback_topics(intent), "source": "fallback"}

        # ── 2. Concatenate titles + abstracts into one text corpus ────
        corpus_parts = []
        for p in papers:
            title = p.get("title", "")
            abstract = p.get("abstract", "")
            if title:
                corpus_parts.append(title)
            if abstract and abstract != "No abstract available" and abstract != "No abstract available.":
                corpus_parts.append(abstract)

        corpus = " ".join(corpus_parts)

        # ── 3. Extract top 3 topics via keyword frequency analysis ───
        topics = extract_top_topics(corpus, query=intent, top_n=3)

        if not topics:
            logger.warning(f"Keyword extraction returned nothing for '{intent}', using fallback.")
            return {"data": _fallback_topics(intent), "source": "fallback"}

        return {"data": topics, "source": "aggregated"}

    except Exception as e:
        logger.error(f"Error in discover_topics: {e}")
        # Return fallback instead of 503 — the feature should never hard-fail
        return {"data": _fallback_topics(intent), "source": "fallback"}
