import logging
from ai.guardrails import validate_input_layers_a_b
from ai.keyword_extractor import extract_top_topics
from integrations.paper_search import search_all
from ai.relevance import _filter_relevant_papers
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
            limit_per_source=10,      # bigger sample = corpus actually reflects the query
            semantic_rerank=True,      # rank by relevance to intent, not just recency
        )

        papers = await _filter_relevant_papers(intent, papers)
        
        if not papers:
            logger.warning(f"No papers found for intent '{intent}', using fallback topics.")
            return {"data": _fallback_topics(intent), "source": "fallback"}

        # ── 2. Keep each paper as its own document (needed for TF-IDF) ────
        docs = []
        for p in papers:
            title = p.get("title", "")
            abstract = p.get("abstract", "")
            if abstract in ("No abstract available", "No abstract available."):
                abstract = ""
            doc = f"{title} {abstract}".strip()
            if doc:
                docs.append(doc)

        # ── 3. Extract top 3 topics via TF-IDF across the paper set ──
        topics = extract_top_topics(docs, query=intent, top_n=3)

        if not topics:
            logger.warning(f"Keyword extraction returned nothing for '{intent}', using fallback.")
            return {"data": _fallback_topics(intent), "source": "fallback"}

        return {"data": topics, "source": "aggregated"}

    except Exception as e:
        logger.error(f"Error in discover_topics: {e}")
        # Return fallback instead of 503 — the feature should never hard-fail
        return {"data": _fallback_topics(intent), "source": "fallback"}
