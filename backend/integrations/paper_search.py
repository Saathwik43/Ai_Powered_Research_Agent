import asyncio
import math
import time
import logging
from datetime import datetime
from integrations.openalex import search_papers as openalex_search
from integrations.arxiv import search_papers as arxiv_search
from integrations.semanticscholar import search_papers as s2_search
from integrations.crossref import search_works as crossref_search
from integrations.github_knowledge import search_github_knowledge
from integrations.pubmed import search_papers as pubmed_search
from integrations.springer import search_papers as springer_search
from integrations.core_api import search_papers as core_search
from integrations.base_search import search_papers as base_search
from integrations.europepmc import search_papers as europepmc_search
from integrations.doaj import search_papers as doaj_search

logger = logging.getLogger(__name__)
_cache = {}
_embedding_cache = {}
_inflight: dict = {}

# limit_per_source is part of the search cache key, so every caller that wants
# to reuse one fan-out has to pass the same value. The Dashboard fires
# /api/literature and /api/topics in parallel for the same query; both use this.
SHARED_LIMIT_PER_SOURCE = 20

# How many top-ranked papers get a semantic embedding for reranking.
RERANK_WINDOW = 30

_CURRENT_YEAR = datetime.now().year

def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x*y for x, y in zip(a, b))
    norm_a = sum(x*x for x in a) ** 0.5
    norm_b = sum(x*x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

def _paper_embedding_text(paper: dict) -> str:
    title = paper.get("title") or ""
    abstract = (paper.get("abstract") or "")[:500]
    return f"{title}. {abstract}"


async def _embed_papers_cached(papers: list) -> list:
    """
    Embeddings for *papers*, aligned by position, with None where unavailable.

    Cache hits cost nothing; every miss goes into a single batched request
    instead of one request per paper. TTL matches the previous per-paper
    behaviour: 600s for a real embedding, 60s for a failure so a transient
    outage is retried soon without hammering the API.
    """
    from ai.llm_provider import get_embeddings_batch

    now = time.time()
    keys = [hash(_paper_embedding_text(p)) for p in papers]
    embeddings: list = [None] * len(papers)
    misses: list[int] = []

    for i, key in enumerate(keys):
        cached = _embedding_cache.get(key)
        if cached and now < cached[1]:
            embeddings[i] = cached[0]
        else:
            misses.append(i)

    if misses:
        texts = [_paper_embedding_text(papers[i]) for i in misses]
        fetched = await get_embeddings_batch(texts, task_type="RETRIEVAL_DOCUMENT")
        for i, emb in zip(misses, fetched):
            embeddings[i] = emb
            _embedding_cache[keys[i]] = (emb, now + (60 if emb is None else 600))

    return embeddings


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation for dedup comparison."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _normalize_doi(doi: str) -> str:
    """Lowercase and strip URL prefixes. Returns '' unless the value is a real DOI."""
    if not doi or not isinstance(doi, str):
        return ""
    cleaned = (
        doi.replace("https://doi.org/", "")
        .replace("http://doi.org/", "")
        .replace("https://dx.doi.org/", "")
        .replace("http://dx.doi.org/", "")
        .replace("doi:", "")
        .strip()
        .lower()
    )
    if not cleaned.startswith("10."):
        return ""
    return cleaned


def _deduplicate(papers: list) -> list:
    """Remove duplicate papers by DOI, falling back to normalized title similarity."""
    seen_dois = set()
    seen_titles = set()
    unique = []
    
    for paper in papers:
        doi = paper.get("doi") or ""
        if not _normalize_doi(doi):
            doi = paper.get("id") or ""
        if not _normalize_doi(doi):
            url = paper.get("url") or ""
            doi = url if "doi.org/" in url else ""
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
    "OpenAlex": 0.7,
    "EuropePMC": 0.65,
    "CORE": 0.65,
    "PubMed": 0.65,
    "Crossref": 0.6,
    "DOAJ": 0.55,
    "arXiv": 0.5,
    "BASE": 0.45,
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
    exclude_sources: set = None,
) -> list:
    """
    Query all configured integrations in parallel using asyncio.
    Aggregates, deduplicates, and ranks results.

    exclude_sources: optional set of source names to skip entirely (e.g.
    {"BASE", "DOAJ"} for topic-discovery, where grey-lit/broad-OA noise
    hurts more than raw coverage helps).
    """
    exclude_sources = set(exclude_sources or set())
    try:
        from services.admin_status import get_disabled_search_tasks
        exclude_sources |= get_disabled_search_tasks()
    except Exception:
        pass
    cache_key = f"{query}_{limit_per_source}_{diversify}_{semantic_rerank}_all"
    if exclude_sources:
        cache_key += f"_{sorted(exclude_sources)}"
    now = time.time()
    if cache_key in _cache:
        cached_data, timestamp = _cache[cache_key]
        if now - timestamp < 600:  # 10 minutes TTL
            logger.info(f"Returning cached literature results for {query}")
            return cached_data

    # Single-flight. The Dashboard fires /api/topics and /api/literature in
    # parallel, so both miss the still-empty cache and would each fan out to
    # every source. Identical concurrent searches share one execution instead.
    # Shielded so a disconnecting client cannot cancel a search others await.
    task = _inflight.get(cache_key)
    if task is None:
        task = asyncio.ensure_future(_execute_search(
            query, limit_per_source, diversify, semantic_rerank,
            source_timeout, oa_timeout, exclude_sources, cache_key,
        ))
        _inflight[cache_key] = task
        task.add_done_callback(lambda t, k=cache_key: _inflight.pop(k, None))
    return await asyncio.shield(task)


async def _execute_search(
    query: str,
    limit_per_source: int,
    diversify: bool,
    semantic_rerank: bool,
    source_timeout: float,
    oa_timeout: float,
    exclude_sources: set,
    cache_key: str,
) -> list:
    """The actual fan-out. Always reached through search_all()."""
    now = time.time()

    def _task(name, coro):
        if name in exclude_sources:
            # The coroutine was already constructed by the call below, so close it
            # explicitly instead of letting it leak as a "never awaited" warning.
            coro.close()
            return None
        return (name, asyncio.create_task(coro, name=name))

    named = [t for t in [
        _task("SemanticScholar", s2_search(query, limit=limit_per_source)),
        _task("OpenAlex",        openalex_search(query, limit=limit_per_source)),
        _task("Crossref",        crossref_search(query, limit=limit_per_source)),
        _task("PubMed",          pubmed_search(query, limit=limit_per_source)),
        _task("arXiv",           arxiv_search(query, limit=limit_per_source)),
        _task("GitHub",          asyncio.to_thread(search_github_knowledge, query)),
        _task("Springer",        springer_search(query, limit=limit_per_source)),
        _task("CORE",            core_search(query, limit=limit_per_source)),
        _task("BASE",            base_search(query, limit=limit_per_source)),
        _task("EuropePMC",       europepmc_search(query, limit=limit_per_source)),
        _task("DOAJ",            doaj_search(query, limit=limit_per_source)),
    ] if t is not None]

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
    core_results     = results_map.get("CORE", [])
    base_results     = results_map.get("BASE", [])
    europepmc_results = results_map.get("EuropePMC", [])
    doaj_results     = results_map.get("DOAJ", [])

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
    # Semantic Scholar, Springer, CORE, BASE, EuropePMC, DOAJ already tag their own in their modules (or we enforce it here if not)
    for p in springer_results:
        p.setdefault("source", "Springer")
    for p in core_results:
        p.setdefault("source", "CORE")
    for p in base_results:
        p.setdefault("source", "BASE")
    for p in europepmc_results:
        p.setdefault("source", "EuropePMC")
    for p in doaj_results:
        p.setdefault("source", "DOAJ")

    # Merge all sources into a single list
    merged = (
        s2_results
        + openalex_results
        + crossref_results
        + pubmed_results
        + arxiv_results
        + github_results
        + springer_results
        + core_results
        + base_results
        + europepmc_results
        + doaj_results
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
                # One request for the query, one for the whole rerank window.
                top_candidates = unique[:RERANK_WINDOW]
                paper_embs = await _embed_papers_cached(top_candidates)

                for p, p_emb in zip(top_candidates, paper_embs):
                    if isinstance(p_emb, list) and p_emb:
                        semantic_score = _cosine_sim(query_emb, p_emb)
                        # Blended score: 0.6 lexical + 0.4 semantic
                        p["_semantic_rank"] = (0.6 * p.get("_relevance_rank", 0.0)) + (0.4 * semantic_score)
                    else:
                        p["_semantic_rank"] = p.get("_relevance_rank", 0.0)

                for p in unique[RERANK_WINDOW:]:
                    p["_semantic_rank"] = p.get("_relevance_rank", 0.0)

                unique.sort(key=lambda p: p.get("_semantic_rank", 0.0), reverse=True)
        except Exception as e:
            logger.warning(f"Semantic reranking failed, falling back to lexical: {e}")

    if diversify:
        unique = _apply_diversity_quota(unique)

    # Enrich with Unpaywall open-access links (non-blocking best-effort, 8s ceiling for large lists)
    try:
        if "Unpaywall" not in exclude_sources:
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
