import logging
from ai.guardrails import validate_input_layers_a_b
from ai.keyword_extractor import extract_top_topics
from integrations.paper_search import SHARED_LIMIT_PER_SOURCE, search_all
logger = logging.getLogger(__name__)

# Upper bound on the TF-IDF corpus, taken from the ranked head.
TOPIC_CORPUS_SIZE = 60

_NOISY_FOR_TOPICS = {"DOAJ"}


def _fallback_topics(intent: str):
    # `query` is the intent itself, not the decorated title: we found nothing
    # to narrow with, so searching "Advancements in X" would only add noise.
    return [
        {"id": 1, "title": f"Advancements in {intent}", "query": intent, "impact": "High"},
        {"id": 2, "title": f"Emerging Applications of {intent}", "query": intent, "impact": "High"},
        {"id": 3, "title": f"Challenges and Future Directions in {intent}", "query": intent, "impact": "Medium"},
    ]


async def discover_topics(intent: str):
    """
    Discover trending research topics by aggregating papers from all
    configured sources (OpenAlex, Semantic Scholar, arXiv, Crossref,
    PubMed, Springer, Europe PMC, DOAJ, GitHub) and extracting the most
    frequent keyword phrases — no LLM required.
    """
    # ── Guardrail check (unchanged) ──────────────────────────────────
    if not validate_input_layers_a_b(intent):
        return {"data": [], "source": "aggregated", "coherence_check": "failed"}

    try:
        # ── 1. Fetch papers from ALL sources (fast, AI-free) ─────────
        # Arguments deliberately match the /api/literature endpoint so the
        # Dashboard's two parallel requests share one search_all cache entry
        # and trigger a single fan-out. DOAJ is dropped afterwards
        # instead of via exclude_sources, which is part of the cache key:
        # grey-lit/broad-OA noise skews topic extraction, but it is fine for
        # the full literature search.
        papers = await search_all(intent, limit_per_source=SHARED_LIMIT_PER_SOURCE)

        papers = [p for p in (papers or []) if p.get("source") not in _NOISY_FOR_TOPICS]

        # Ranked head only. TF-IDF wants a clean, bounded corpus, and the
        # per-paper relevance classifier is pure overhead here: these papers
        # are never shown to the user, only mined for keyword phrases.
        papers = papers[:TOPIC_CORPUS_SIZE]

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
