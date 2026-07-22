import asyncio
import math
import time
import logging
from integrations.openalex import search_papers as openalex_search
from integrations.arxiv import search_papers as arxiv_search
from integrations.semanticscholar import search_papers as s2_search
from integrations.crossref import search_works as crossref_search
from integrations.github_knowledge import search_github_knowledge
from integrations.pubmed import search_papers as pubmed_search
from integrations.springer import search_papers as springer_search
from integrations.ieee import search_papers as ieee_search
from integrations.core_api import search_papers as core_search

logger = logging.getLogger(__name__)
_cache = {}
_embedding_cache = {}

_CURRENT_YEAR = 2026

def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x*y for x, y in zip(a, b))
    norm_a = sum(x*x for x in a) ** 0.5
    norm_b = sum(x*x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

async def _get_paper_embedding(paper: dict) -> list[float] | None:
    from ai.llm_provider import get_embedding
    title = paper.get("title") or ""
    abstract = (paper.get("abstract") or "")[:500]
    text = f"{title}. {abstract}"
    key = hash(text)
    
    now = time.time()
    if key in _embedding_cache:
        emb, expires_at = _embedding_cache[key]
        if now < expires_at:
            return emb
            
    emb = await get_embedding(text, task_type="RETRIEVAL_DOCUMENT")
    expires_at = now + 60 if emb is None else now + 600
    _embedding_cache[key] = (emb, expires_at)
    return emb


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation for dedup comparison."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _normalize_doi(doi: str) -> str:
    """Lowercase and strip URL prefixes for DOI."""
    if not doi:
        return ""
    return doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip().lower()


def _deduplicate(papers: list) -> list:
    """Remove duplicate papers by DOI, falling back to normalized title similarity."""
    seen_dois = set()
    seen_titles = set()
    unique = []
    
    for paper in papers:
        doi = paper.get("doi", paper.get("url", ""))
        norm_doi = _normalize_doi(doi)
        
        # If we have a valid DOI, try deduplicating by DOI
        if norm_doi:
            if norm_doi in seen_dois:
                continue
        
        # Fall back to title deduplication if no DOI or it's a new DOI
        title = paper.get("title", "")
        norm_title = _normalize_title(title)[:60]
        
        if not norm_title or norm_title in seen_titles:
            # If title is empty or already seen, we consider it a duplicate (even if DOI is new, to be safe)
            if not norm_doi: # Only skip if there's also no DOI to match on
                continue
            elif norm_title in seen_titles:
                continue
                
        # Unique paper
        seen_titles.add(norm_title)
        if norm_doi:
            seen_dois.add(norm_doi)
        unique.append(paper)
        
    return unique


# ─── Relevance-Based Scoring ──────────────────────────────────────────────────

_SOURCE_WEIGHTS = {
    "Semantic Scholar": 0.9,
    "SemanticScholar": 0.9,
    "Springer": 0.85,
    "IEEE": 0.8,
    "OpenAlex": 0.7,
    "CORE": 0.65,
    "PubMed": 0.65,
    "Crossref": 0.6,
    "arXiv": 0.5,
}
# GitHub sub-sources all start with "GitHub/"
_GITHUB_SOURCE_WEIGHT = 0.3


_STOPWORDS = {"and", "the", "of", "in", "for", "a", "an", "to", "on", "with", "is", "by", "from", "based", "using"}

def _get_keywords(text: str) -> set:
    import re
    words = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
    return set(w for w in words if w not in _STOPWORDS and len(w) > 2)

def _compute_score(query: str, paper: dict) -> float:
    """
    Combined relevance score from five signals:
      - Text Match Score (40% weight): keyword overlap with title/abstract
      - Citation count (25% weight): log-scaled
      - Recency bonus (20% weight): papers from last 10 years boosted
      - Source relevance weight (10% weight)
      - Open Access Boost (5% weight): immediate PDF availability
    """
    # 1. Text Match Score (40% weight)
    query_kw = _get_keywords(query)
    text_match_score = 0.0
    if query_kw:
        title_kw = _get_keywords(paper.get("title", ""))
        abs_kw = _get_keywords(paper.get("abstract", ""))
        
        # Calculate overlap (percentage of query keywords present)
        title_overlap = len(query_kw.intersection(title_kw)) / len(query_kw)
        abs_overlap = len(query_kw.intersection(abs_kw)) / len(query_kw)
        
        # Weight title more heavily
        text_match_score = min(title_overlap * 0.7 + abs_overlap * 0.3, 1.0)
    else:
        text_match_score = 0.5 # Default if no valid keywords extracted

    # 2. Citation signal — log-scaled, capped at 1.0 (25% weight)
    citations = paper.get("citations", 0) or 0
    citation_score = min(math.log1p(citations) / 10.0, 1.0)

    # 3. Source relevance weight (10% weight)
    source = paper.get("source", "")
    if source.startswith("GitHub"):
        source_score = _GITHUB_SOURCE_WEIGHT
    else:
        source_score = _SOURCE_WEIGHTS.get(source, 0.4)

    # 4. Recency bonus — linear decay over 10 years (20% weight)
    year = paper.get("year", "")
    try:
        recency = max(0.0, 1.0 - (_CURRENT_YEAR - int(year)) / 10.0) if str(year).isdigit() else 0.3
    except (ValueError, TypeError):
        recency = 0.3
        
    # 5. Open Access Boost (5% weight)
    oa_boost = 1.0 if (paper.get("oa_url") or paper.get("pdf_url") or paper.get("openAccessPdf")) else 0.0

    return (0.40 * text_match_score) + (0.25 * citation_score) + (0.20 * recency) + (0.10 * source_score) + (0.05 * oa_boost)


def _rank_papers(query: str, papers: list) -> list:
    """Sort papers by combined relevance score (descending)."""
    for p in papers:
        p["_relevance_rank"] = round(_compute_score(query, p), 4)
    papers.sort(key=lambda p: p["_relevance_rank"], reverse=True)
    return papers

def _apply_diversity_quota(papers: list) -> list:
    """Greedily pick top-scored papers but skip/defer a paper if its source already has 9+ picks in the top-15."""
    diverse = []
    deferred = []
    source_counts = {}
    
    for p in papers:
        source = p.get("source", "Unknown")
        if source.startswith("GitHub"):
            source = "GitHub"
            
        if len(diverse) < 15:
            if source_counts.get(source, 0) >= 9:
                deferred.append(p)
            else:
                diverse.append(p)
                source_counts[source] = source_counts.get(source, 0) + 1
        else:
            deferred.append(p)
            
    return diverse + deferred


async def search_all(
    query: str,
    limit_per_source: int = 15,
    diversify: bool = False,
    semantic_rerank: bool = True,
    source_timeout: float = 20.0,
    oa_timeout: float = 8.0,
) -> list:
    """
    Query all configured integrations in parallel using asyncio.
    Aggregates, deduplicates, and ranks results.
    """
    cache_key = f"{query}_{limit_per_source}_all"
    now = time.time()
    if cache_key in _cache:
        cached_data, timestamp = _cache[cache_key]
        if now - timestamp < 600:  # 10 minutes TTL
            logger.info(f"Returning cached literature results for {query}")
            return cached_data

    named = [
        ("SemanticScholar", asyncio.create_task(s2_search(query, limit=limit_per_source),       name="SemanticScholar")),
        ("OpenAlex",        asyncio.create_task(openalex_search(query, limit=limit_per_source),  name="OpenAlex")),
        ("Crossref",        asyncio.create_task(crossref_search(query, limit=limit_per_source),  name="Crossref")),
        ("PubMed",          asyncio.create_task(pubmed_search(query, limit=limit_per_source),    name="PubMed")),
        ("arXiv",           asyncio.create_task(arxiv_search(query, limit=limit_per_source),     name="arXiv")),
        ("GitHub",          asyncio.create_task(
                                asyncio.to_thread(search_github_knowledge, query),    name="GitHub")),
        ("Springer",        asyncio.create_task(springer_search(query, limit=limit_per_source), name="Springer")),
        ("IEEE",            asyncio.create_task(ieee_search(query, limit=limit_per_source), name="IEEE")),
        ("CORE",            asyncio.create_task(core_search(query, limit=limit_per_source), name="CORE")),
    ]

    task_to_name = {task: name for name, task in named}
    all_tasks = {task for _, task in named}

    # Bound aggregate source latency while still returning fast partial results.
    done, pending = await asyncio.wait(all_tasks, timeout=source_timeout)

    # Cancel only the stragglers — tasks that already finished are untouched.
    if pending:
        slow_names = [task_to_name[t] for t in pending]
        logger.warning(
            f"search_all() {source_timeout:g}s ceiling: cancelling {len(pending)} slow source(s): "
            f"{slow_names}.  Returning partial results from {len(done)} fast source(s)."
        )
        for task in pending:
            task.cancel()
        # Drain cancellations so no dangling coroutines remain.
        await asyncio.gather(*pending, return_exceptions=True)

    # If every source timed out (done is empty), fall back to stale cache or [].
    if not done:
        logger.warning("search_all(): all sources timed out, returning stale cache or [].")
        cached = _cache.get(cache_key)
        return cached[0] if cached else []

    # Harvest results from the completed tasks; catch per-task exceptions.
    results_map: dict[str, list] = {name: [] for name, _ in named}
    for task in done:
        name = task_to_name[task]
        exc = task.exception()
        if exc is not None:
            logger.error(f"Search task failed ({name}): {exc}")
        else:
            results_map[name] = task.result() or []

    s2_results       = results_map.get("SemanticScholar", [])
    openalex_results = results_map.get("OpenAlex", [])
    crossref_results = results_map.get("Crossref", [])
    pubmed_results   = results_map.get("PubMed", [])
    arxiv_results    = results_map.get("arXiv", [])
    github_results   = results_map.get("GitHub", [])
    springer_results = results_map.get("Springer", [])
    ieee_results     = results_map.get("IEEE", [])
    core_results     = results_map.get("CORE", [])

    # Tag sources that don't already have one
    for p in openalex_results:
        p.setdefault("source", "OpenAlex")
    for p in arxiv_results:
        p.setdefault("source", "arXiv")
    for p in github_results:
        p.setdefault("source", p.get("source", "GitHub"))
    for p in crossref_results:
        p.setdefault("source", "Crossref")
    for p in pubmed_results:
        p.setdefault("source", "PubMed")
    # Semantic Scholar, IEEE, Springer, CORE already tag their own in their modules (or we enforce it here if not)
    for p in springer_results:
        p.setdefault("source", "Springer")
    for p in ieee_results:
        p.setdefault("source", "IEEE")
    for p in core_results:
        p.setdefault("source", "CORE")

    # Merge all sources into a single list
    merged = (
        s2_results
        + openalex_results
        + crossref_results
        + pubmed_results
        + arxiv_results
        + github_results
        + springer_results
        + ieee_results
        + core_results
    )

    # Deduplicate
    unique = _deduplicate(merged)

    # Rank by combined lexical relevance score instead of source order
    unique = _rank_papers(query, unique)
    
    if semantic_rerank:
        try:
            from ai.llm_provider import get_embedding
            query_emb = await get_embedding(query, task_type="RETRIEVAL_QUERY")
            if query_emb:
                # Top ~30 papers for semantic reranking
                top_candidates = unique[:30]
                tasks = [_get_paper_embedding(p) for p in top_candidates]
                paper_embs = await asyncio.gather(*tasks, return_exceptions=True)
                
                for p, p_emb in zip(top_candidates, paper_embs):
                    if isinstance(p_emb, list) and p_emb:
                        semantic_score = _cosine_sim(query_emb, p_emb)
                        # Blended score: 0.6 lexical + 0.4 semantic
                        p["_semantic_rank"] = (0.6 * p.get("_relevance_rank", 0.0)) + (0.4 * semantic_score)
                    else:
                        p["_semantic_rank"] = p.get("_relevance_rank", 0.0)
                        
                for p in unique[30:]:
                    p["_semantic_rank"] = p.get("_relevance_rank", 0.0)
                    
                unique.sort(key=lambda p: p.get("_semantic_rank", 0.0), reverse=True)
        except Exception as e:
            logger.warning(f"Semantic reranking failed, falling back to lexical: {e}")

    if diversify:
        unique = _apply_diversity_quota(unique)

    # Enrich with Unpaywall open-access links (non-blocking best-effort, 8s ceiling for large lists)
    try:
        from integrations.unpaywall import enrich_papers_with_oa
        unique = await asyncio.wait_for(enrich_papers_with_oa(unique), timeout=oa_timeout)
    except asyncio.TimeoutError:
        import logging
        logging.getLogger(__name__).warning(f"Unpaywall enrichment exceeded {oa_timeout:g}s ceiling, returning unenriched results.")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Unpaywall enrichment failed (non-fatal): {e}")

    _cache[cache_key] = (unique, now)
    return unique
