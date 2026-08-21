import asyncio
import hashlib
import math
import re
import time
import logging
from dataclasses import dataclass, field
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
from core.query_key import canonical_key
from core.paper_identity import (
    identity_keys,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    paper_doi,
)
from core.ttl_cache import TTLCache
from services import semantic_cache

logger = logging.getLogger(__name__)
# Bounded so a long-lived process cannot accumulate results and 3072-float
# embedding vectors forever. The TTL here is a *retention* ceiling; the
# freshness checks at the call sites are shorter and still authoritative —
# search results stay readable past 600s specifically so a total source outage
# can fall back to a stale entry rather than returning nothing.
_cache = TTLCache(maxsize=500, ttl=1800)
_embedding_cache = TTLCache(maxsize=5000, ttl=900)
_inflight: dict = {}

# limit_per_source is part of the search cache key, so every caller that wants
# to reuse one fan-out has to pass the same value. The Dashboard fires
# /api/literature and /api/topics in parallel for the same query; both use this.
SHARED_LIMIT_PER_SOURCE = 20

# How many top-ranked papers get a semantic embedding for reranking.
RERANK_WINDOW = 30

# Fan-out order. Outcomes are reported in this order so a PRISMA-style
# "which databases were searched" line is stable across cache hits.
SOURCE_NAMES = (
    "SemanticScholar",
    "OpenAlex",
    "Crossref",
    "PubMed",
    "arXiv",
    "GitHub",
    "Springer",
    "CORE",
    "BASE",
    "EuropePMC",
    "DOAJ",
)

def _current_year() -> int:
    """Read the year per call — a module constant goes stale on New Year in a
    long-running process and silently skews every recency score after that."""
    return datetime.now().year

def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x*y for x, y in zip(a, b))
    norm_a = sum(x*x for x in a) ** 0.5
    norm_b = sum(x*x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

def _paper_embedding_text(paper: dict) -> str:
    title = paper.get("title") or ""
    abstract = (paper.get("abstract") or "")[:500]
    return f"{title}. {abstract}"


def _embedding_cache_key(text: str) -> str:
    """Stable content digest — survives restarts and is safe to share."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


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
    # Content digest, not hash(): str.__hash__ is salted per process, so the
    # same text keys differently after a restart and the cache can never be
    # shared across workers or persisted.
    keys = [_embedding_cache_key(_paper_embedding_text(p)) for p in papers]
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


# Identity lives in core/paper_identity.py, shared with ai/relevance.py so the
# dedupe index and the relevance-verdict cache cannot drift apart. Aliased here
# because this module's callers and tests already speak these names.
_normalize_title = normalize_title
_normalize_doi = normalize_doi
_paper_doi = paper_doi
_normalize_arxiv_id = normalize_arxiv_id
_identity_keys = identity_keys

# Placeholder values integrations emit when a field is unavailable. Treated as
# absent when merging duplicates so a real value always wins.
_PLACEHOLDERS = {
    "", "unknown", "unknown authors", "untitled",
    "no abstract available", "no abstract available.",
}


def _is_present(value) -> bool:
    """True when *value* carries real information rather than a placeholder."""
    if value is None or value == [] or value == {}:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _PLACEHOLDERS
    return True


def _citation_count(paper: dict) -> int:
    try:
        return int(paper.get("citations") or 0)
    except (TypeError, ValueError):
        return 0


def _merge_into(kept: dict, other: dict) -> None:
    """
    Fold *other* into *kept* in place, keeping *kept*'s position in the result.

    The more-cited record wins field conflicts — it is almost always the
    published version, which carries better metadata than the preprint. The
    loser still fills in anything the winner is missing (typically the
    preprint's free ``pdf_url``), so merging strictly adds information.
    """
    kept_citations = _citation_count(kept)
    other_citations = _citation_count(other)
    authoritative, secondary = (other, kept) if other_citations > kept_citations else (kept, other)

    merged = {}
    for source in (secondary, authoritative):
        for key, value in source.items():
            if _is_present(value):
                merged[key] = value
    # Preserve any private ranking fields already attached to kept.
    for key, value in kept.items():
        if key.startswith("_"):
            merged[key] = value
    merged["citations"] = max(kept_citations, other_citations)

    kept.clear()
    kept.update(merged)


def _deduplicate(papers: list) -> list:
    """
    Collapse records that describe the same paper, merging their metadata.

    Identity is DOI, arXiv id, or normalized title — sharing *any* of the three
    is a match. Duplicates are merged rather than discarded, so the surviving
    record is the union of what every source knew about the paper.

    A record with no usable identifier at all (no DOI, no arXiv id, no title)
    is dropped: it cannot be deduplicated, cited, or opened.
    """
    unique: list = []
    index_by_key: dict[str, int] = {}

    for paper in papers:
        keys = _identity_keys(paper)
        if not keys:
            continue

        hit = next((index_by_key[k] for k in keys if k in index_by_key), None)
        if hit is None:
            unique.append(paper)
            position = len(unique) - 1
        else:
            _merge_into(unique[hit], paper)
            position = hit
            # The merge can add identifiers (e.g. the preprint contributed an
            # arXiv id), so re-register to catch a third copy arriving later.
            keys = _identity_keys(unique[hit])

        for key in keys:
            # setdefault, not assignment: the first record to claim a key keeps
            # it, so a merge can never steal another paper's identity.
            index_by_key.setdefault(key, position)

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
        recency = max(0.0, 1.0 - (_current_year() - int(year)) / 10.0) if str(year).isdigit() else 0.3
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

# A `diversify` flag once gated a source quota here (max 9 papers per source in
# the top 15). No caller ever set it, the 15/9 numbers ignored the requested
# limit, and the dead flag still widened the search cache key — two callers
# differing only in a parameter that did nothing paid two full 11-source
# fan-outs. Flag and quota removed together (audit A12). Source balance, if it
# is wanted again, belongs in _compute_score where it can see the real limit.


@dataclass(frozen=True)
class SourceOutcome:
    """What one database did for this query.

    status:
      ok       — parsed at least one record
      empty    — the source answered, nothing matched
      error    — exception / unusable response
      timeout  — cancelled by the aggregate ceiling
      skipped  — disabled or excluded before the request
    """

    name: str
    status: str
    count: int = 0
    ms: int | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        out = {"name": self.name, "status": self.status, "count": self.count}
        if self.ms is not None:
            out["ms"] = self.ms
        if self.error:
            out["error"] = self.error
        return out


@dataclass(frozen=True)
class SearchMeta:
    """
    How a result was obtained.

    ``matched_query`` is set only when the semantic cache answered with a
    *different* query's results. Callers must surface it — see
    services/semantic_cache.py on why silent substitution is not acceptable.

    ``sources`` is the per-database yield for this search. A result set where
    CORE timed out is no longer indistinguishable from one where CORE returned
    20 papers — that was the gap the API integration audit called out as the
    highest value-per-line fix (PRISMA 2.6).
    """

    cache: str  # "miss" | "exact" | "semantic"
    matched_query: str | None = None
    similarity: float | None = None
    sources: tuple[SourceOutcome, ...] = field(default_factory=tuple)

    def sources_as_dicts(self) -> list[dict]:
        return [s.as_dict() for s in self.sources]


def _unpack_cache_entry(entry) -> tuple[list, float, tuple[SourceOutcome, ...]]:
    """Accept both the old (papers, stored_at) tuple and the current 3-tuple."""
    if not isinstance(entry, tuple) or len(entry) < 2:
        return [], 0.0, ()
    papers, stored_at = entry[0], entry[1]
    sources = entry[2] if len(entry) >= 3 else ()
    return papers or [], stored_at or 0.0, tuple(sources or ())


def _split_fan_out(result) -> tuple[list, tuple[SourceOutcome, ...]]:
    """_execute_search returns (papers, sources). Tests may still stub a bare list."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], list):
        papers, sources = result
        return papers, tuple(sources or ())
    return result or [], ()


def _ordered_outcomes(by_name: dict[str, SourceOutcome]) -> tuple[SourceOutcome, ...]:
    ordered = [by_name[name] for name in SOURCE_NAMES if name in by_name]
    extras = [outcome for name, outcome in by_name.items() if name not in SOURCE_NAMES]
    return tuple(ordered + extras)


async def search_all(
    query: str,
    limit_per_source: int = 15,
    semantic_rerank: bool = True,
    source_timeout: float = 20.0,
    oa_timeout: float = 8.0,
    exclude_sources: set = None,
    allow_semantic_cache: bool = True,
) -> list:
    """
    Query all configured integrations in parallel and return ranked papers.

    Thin wrapper over search_all_with_meta() for callers that do not need to
    know how the result was obtained.
    """
    papers, _meta = await search_all_with_meta(
        query, limit_per_source, semantic_rerank,
        source_timeout, oa_timeout, exclude_sources, allow_semantic_cache,
    )
    return papers


async def search_all_with_meta(
    query: str,
    limit_per_source: int = 15,
    semantic_rerank: bool = True,
    source_timeout: float = 20.0,
    oa_timeout: float = 8.0,
    exclude_sources: set = None,
    allow_semantic_cache: bool = True,
) -> tuple[list, SearchMeta]:
    """
    Query all configured integrations in parallel using asyncio.
    Aggregates, deduplicates, and ranks results.

    Resolution order: exact canonical cache → semantic cache → real fan-out.

    exclude_sources: optional set of source names to skip entirely (e.g.
    {"BASE", "DOAJ"} for topic-discovery, where grey-lit/broad-OA noise
    hurts more than raw coverage helps).

    allow_semantic_cache: set False to force a real search for this query —
    what the UI's "search instead for X" escape hatch sends.
    """
    exclude_sources = set(exclude_sources or set())
    try:
        from services.admin_status import get_disabled_search_tasks
        exclude_sources |= get_disabled_search_tasks()
    except Exception:
        pass
    # Canonical identity, not the raw string: "Machine Learning", "machine
    # learning" and a Title-Cased topic label from the Dashboard are one entry
    # and one fan-out. The raw *query* still goes to the sources and to
    # _rank_papers below — only the cache key is canonicalised.
    bucket_key = f"{limit_per_source}_{semantic_rerank}"
    if exclude_sources:
        bucket_key += f"_{sorted(exclude_sources)}"
    cache_key = f"{canonical_key(query)}_{bucket_key}_all"
    now = time.time()
    if cache_key in _cache:
        entry = _cache[cache_key]
        papers, stored_at, sources = _unpack_cache_entry(entry)
        if now - stored_at < 600:  # 10 minutes TTL
            logger.info(f"Returning cached literature results for {query}")
            return papers, SearchMeta(cache="exact", sources=sources)

    # Single-flight. The Dashboard fires /api/topics and /api/literature in
    # parallel, so both miss the still-empty cache and would each fan out to
    # every source. Identical concurrent searches share one execution instead.
    # Shielded so a disconnecting client cannot cancel a search others await.
    #
    # This must cover the ENTIRE resolve path, embedding lookup included.
    # Awaiting anything between the cache check and this registration lets two
    # concurrent identical searches both observe an empty _inflight and both
    # fan out — which is exactly what happened when the semantic-cache lookup
    # was awaited above this point.
    inflight_key = f"{cache_key}|semantic={allow_semantic_cache}"
    task = _inflight.get(inflight_key)
    if task is None:
        task = asyncio.ensure_future(_resolve_search(
            query, limit_per_source, semantic_rerank,
            source_timeout, oa_timeout, exclude_sources, cache_key,
            bucket_key, allow_semantic_cache,
        ))
        _inflight[inflight_key] = task
        task.add_done_callback(lambda t, k=inflight_key: _inflight.pop(k, None))
    return await asyncio.shield(task)


async def _resolve_search(
    query: str,
    limit_per_source: int,
    semantic_rerank: bool,
    source_timeout: float,
    oa_timeout: float,
    exclude_sources: set,
    cache_key: str,
    bucket_key: str,
    allow_semantic_cache: bool,
) -> tuple[list, SearchMeta]:
    """Semantic-cache lookup, then the real fan-out. Runs under single-flight."""
    # The query embedding is needed by the rerank step anyway, so fetching it
    # here costs nothing on a true miss — it is handed to _execute_search
    # rather than fetched twice.
    query_embedding = await _query_embedding(query) if semantic_rerank else None

    if allow_semantic_cache and query_embedding:
        hit = semantic_cache.lookup(bucket_key, cache_key, query_embedding)
        if hit is not None:
            papers = hit.papers
            if hit.needs_rerank:
                # Below the verbatim threshold the stored *ordering* is not
                # trusted, only the candidate pool. Re-ranking against this
                # query costs no network call: paper embeddings are already
                # cached by content digest.
                papers = await _rank_and_rerank(query, papers, query_embedding)
            return papers, SearchMeta(
                cache="semantic",
                matched_query=hit.matched_query,
                similarity=round(hit.similarity, 4),
                sources=hit.sources,
            )

    fan_out = await _execute_search(
        query, limit_per_source, semantic_rerank,
        source_timeout, oa_timeout, exclude_sources, cache_key,
        bucket_key, query_embedding,
    )
    papers, sources = _split_fan_out(fan_out)
    return papers, SearchMeta(cache="miss", sources=sources)


async def _query_embedding(query: str) -> list | None:
    """Embedding for *query*, or None when embeddings are unavailable."""
    try:
        from ai.llm_provider import get_embedding
        return await get_embedding(query, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        logger.warning(f"Query embedding failed, semantic cache disabled for this search: {e}")
        return None


async def _rank_and_rerank(query: str, papers: list, query_embedding: list | None) -> list:
    """
    Lexical rank, then blend in semantic similarity over the rerank window.

    Shared by the fan-out path and the semantic cache's rerank tier so both
    produce identically-scored orderings.
    """
    papers = _rank_papers(query, papers)

    if not query_embedding:
        return papers

    try:
        top_candidates = papers[:RERANK_WINDOW]
        paper_embs = await _embed_papers_cached(top_candidates)

        # Every paper is scored on the SAME scale: 0.6 lexical + 0.4 semantic,
        # with semantic = 0 wherever it is unknown (outside the rerank window,
        # or embedding unavailable).
        #
        # Leaving unscored papers on raw lexical inverted the ranking at the
        # window boundary: paper 31 at lexical 0.70 beat paper 5 whose blend
        # was 0.6*0.70 + 0.4*0.50 = 0.62, purely because it was never reranked.
        for p, p_emb in zip(top_candidates, paper_embs):
            semantic_score = _cosine_sim(query_embedding, p_emb) if isinstance(p_emb, list) and p_emb else 0.0
            p["_semantic_rank"] = (0.6 * p.get("_relevance_rank", 0.0)) + (0.4 * semantic_score)

        for p in papers[RERANK_WINDOW:]:
            p["_semantic_rank"] = 0.6 * p.get("_relevance_rank", 0.0)

        papers.sort(key=lambda p: p.get("_semantic_rank", 0.0), reverse=True)
    except Exception as e:
        logger.warning(f"Semantic reranking failed, falling back to lexical: {e}")

    return papers


async def _execute_search(
    query: str,
    limit_per_source: int,
    semantic_rerank: bool,
    source_timeout: float,
    oa_timeout: float,
    exclude_sources: set,
    cache_key: str,
    bucket_key: str = "",
    query_embedding: list | None = None,
) -> tuple[list, tuple[SourceOutcome, ...]]:
    """The actual fan-out. Always reached through search_all_with_meta()."""
    now = time.time()
    started = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    skipped: list[str] = []

    def _task(name, coro):
        if name in exclude_sources:
            # The coroutine was already constructed by the call below, so close it
            # explicitly instead of letting it leak as a "never awaited" warning.
            coro.close()
            skipped.append(name)
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
    by_name: dict[str, SourceOutcome] = {
        name: SourceOutcome(name=name, status="skipped") for name in skipped
    }

    # Bound aggregate source latency while still returning fast partial results.
    done, pending = await asyncio.wait(all_tasks, timeout=source_timeout) if all_tasks else (set(), set())

    # Cancel only the stragglers — tasks that already finished are untouched.
    if pending:
        slow_names = [task_to_name[t] for t in pending]
        logger.warning(
            f"search_all() {source_timeout:g}s ceiling: cancelling {len(pending)} slow source(s): "
            f"{slow_names}.  Returning partial results from {len(done)} fast source(s)."
        )
        timeout_ms = _elapsed_ms()
        for task in pending:
            name = task_to_name[task]
            by_name[name] = SourceOutcome(
                name=name, status="timeout", ms=timeout_ms,
                error=f"exceeded {source_timeout:g}s",
            )
            task.cancel()
        # Drain cancellations so no dangling coroutines remain.
        await asyncio.gather(*pending, return_exceptions=True)

    # If every source timed out (done is empty), fall back to stale cache or [].
    if all_tasks and not done:
        logger.warning("search_all(): all sources timed out, returning stale cache or [].")
        cached = _cache.get(cache_key)
        if cached:
            papers, _, cached_sources = _unpack_cache_entry(cached)
            return papers, cached_sources or _ordered_outcomes(by_name)
        return [], _ordered_outcomes(by_name)

    # Harvest results from the completed tasks; catch per-task exceptions.
    results_map: dict[str, list] = {name: [] for name, _ in named}
    harvest_ms = _elapsed_ms()
    for task in done:
        name = task_to_name[task]
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            results_map[name] = []
            by_name[name] = SourceOutcome(
                name=name, status="timeout", ms=harvest_ms,
                error=f"exceeded {source_timeout:g}s",
            )
            continue
        if exc is not None:
            logger.error(f"Search task failed ({name}): {exc}")
            results_map[name] = []
            by_name[name] = SourceOutcome(
                name=name, status="error", ms=harvest_ms, error=str(exc)[:240],
            )
        else:
            papers = task.result() or []
            results_map[name] = papers
            by_name[name] = SourceOutcome(
                name=name,
                status="ok" if papers else "empty",
                count=len(papers),
                ms=harvest_ms,
            )

    sources = _ordered_outcomes(by_name)

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

    # Rank by combined relevance score instead of source order. The query
    # embedding was already fetched by the caller for the semantic-cache
    # lookup, so this reuses it rather than paying for a second request.
    if semantic_rerank and query_embedding is None:
        query_embedding = await _query_embedding(query)
    unique = await _rank_and_rerank(query, unique, query_embedding if semantic_rerank else None)

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

    _cache[cache_key] = (unique, now, sources)
    # Remember the meaning of this query too, so a paraphrase can reuse it.
    if query_embedding:
        semantic_cache.store(bucket_key, cache_key, query_embedding, query, unique, sources=sources)
    return unique, sources
