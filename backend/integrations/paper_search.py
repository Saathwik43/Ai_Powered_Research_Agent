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
from integrations.europepmc import search_papers as europepmc_search
from integrations.doaj import search_papers as doaj_search
from core.query_key import canonical_key
from core.paper_identity import (
    identity_keys,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    paper_doi,
    paper_identity,
)
from core import shared_store
from core.ttl_cache import TTLCache
from integrations import registry
from services import api_health, semantic_cache

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
# "which databases were searched" line is stable across cache hits. Derived from
# the registry rather than repeated here — the two drifting apart is how a
# source ends up searched but never reported (or the reverse).
SOURCE_NAMES = registry.source_names()

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

    Three tiers. The in-process cache is keyed by a digest of the exact text
    that would be embedded, so it can only ever return the right vector. Behind
    it sits the durable store (1.3), keyed by **paper identity** — DOI, else
    arXiv id, else the normalised title — so the same paper embeds once, ever,
    across restarts and workers, even when a different source supplied a
    slightly different abstract. Only what neither tier has reaches the API,
    batched into as few requests as possible.

    A failure is cached briefly (60s) in memory and never persisted: a transient
    outage must not write "this paper has no embedding" into a store that has no
    expiry.
    """
    from ai.llm_provider import get_embeddings_batch, EMBEDDING_MODEL

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
        misses = await _fill_from_embedding_store(
            papers, keys, embeddings, misses, EMBEDDING_MODEL, now
        )

    if misses:
        texts = [_paper_embedding_text(papers[i]) for i in misses]
        fetched = await get_embeddings_batch(texts, task_type="RETRIEVAL_DOCUMENT")
        durable: dict = {}
        for i, emb in zip(misses, fetched):
            embeddings[i] = emb
            _embedding_cache[keys[i]] = (emb, now + (60 if emb is None else 600))
            if emb:
                durable[shared_store.embedding_key(
                    paper_identity(papers[i]), EMBEDDING_MODEL, "RETRIEVAL_DOCUMENT"
                )] = emb
        if durable:
            await shared_store.put_embeddings(durable)

    return embeddings


async def _fill_from_embedding_store(
    papers: list, keys: list, embeddings: list, misses: list[int], model: str, now: float
) -> list[int]:
    """Serve what the durable store already knows; return the still-missing positions."""
    if not shared_store.enabled():
        return misses

    identity_by_position = {
        i: shared_store.embedding_key(paper_identity(papers[i]), model, "RETRIEVAL_DOCUMENT")
        for i in misses
    }
    stored = await shared_store.get_embeddings(set(identity_by_position.values()))
    if not stored:
        return misses

    still_missing = []
    for i in misses:
        vector = stored.get(identity_by_position[i])
        if vector:
            embeddings[i] = vector
            _embedding_cache[keys[i]] = (vector, now + 600)
        else:
            still_missing.append(i)
    return still_missing


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
    # PubMed's own wording. Long enough to beat a real abstract on length, so
    # it has to be named here rather than left to look like content.
    "abstract not available via pubmed summary api.",
}


def _is_present(value) -> bool:
    """True when *value* carries real information rather than a placeholder."""
    if value is None or value == [] or value == {}:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _PLACEHOLDERS
    return True


def _longest_abstract(*papers: dict) -> str:
    """The fullest abstract among duplicate records of one paper.

    A truncated abstract is indistinguishable from a short one, so length is
    the only signal available — and it is the right one: no source returns a
    *longer* abstract than the real thing.
    """
    best = ""
    for paper in papers:
        value = paper.get("abstract")
        if isinstance(value, str) and _is_present(value) and len(value.strip()) > len(best):
            best = value.strip()
    return best


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

    ``abstract`` is the exception: it is taken by *length*, not by citation
    count. Crossref and PubMed routinely return a one-line stub for a paper
    whose arXiv record carries the full abstract, and the stub usually has the
    higher citation count — so the rule above was discarding the complete
    abstract we had already paid to fetch and leaving the card with a sentence
    to summarise. Every downstream reader of a paper (card briefings, relevance
    classification, evidence extraction) sees only this one merged record.
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

    abstract = _longest_abstract(kept, other)
    if abstract:
        merged["abstract"] = abstract

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
    "PubMed": 0.65,
    "Crossref": 0.6,
    "DOAJ": 0.55,
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
    Springer timed out is no longer indistinguishable from one where Springer
    returned 20 papers — that was the gap the API integration audit called out as the
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
    {"DOAJ"} for topic-discovery, where grey-lit/broad-OA noise
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
    """Durable cache, semantic cache, then the real fan-out. Under single-flight."""
    # The durable tier before the embedding call, not after: on a restart the
    # results are already there, and paying for a query embedding just to
    # discover that would be the wrong order.
    shared = await _shared_exact_lookup(cache_key)
    if shared is not None:
        papers, sources = shared
        _cache[cache_key] = (papers, time.time(), sources)
        logger.info(f"Returning shared-cache literature results for {query}")
        return papers, SearchMeta(cache="exact", sources=sources)

    # The query embedding is needed by the rerank step anyway, so fetching it
    # here costs nothing on a true miss — it is handed to _execute_search
    # rather than fetched twice.
    query_embedding = await _query_embedding(query) if semantic_rerank else None

    if allow_semantic_cache and query_embedding:
        hit = await semantic_cache.lookup_shared(bucket_key, cache_key, query_embedding)
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


# The durable copy of a fan-out. Same 600s freshness window the in-memory tier
# enforces, so "restarted" and "did not restart" answer identically.
SHARED_SEARCH_TTL = 600


async def _shared_exact_lookup(cache_key: str) -> tuple[list, tuple[SourceOutcome, ...]] | None:
    """The stored fan-out for *cache_key* from the durable tier, or None."""
    payload = await shared_store.get(shared_store.NS_SEARCH, cache_key)
    if not isinstance(payload, dict):
        return None
    papers = payload.get("papers")
    if not isinstance(papers, list) or not papers:
        return None
    return papers, _outcomes_from_dicts(payload.get("sources"))


async def _shared_exact_store(cache_key: str, papers: list, sources: tuple) -> None:
    if not papers:
        return
    await shared_store.set(
        shared_store.NS_SEARCH,
        cache_key,
        {"papers": papers, "sources": [s.as_dict() for s in sources]},
        SHARED_SEARCH_TTL,
    )


def _outcomes_from_dicts(raw) -> tuple[SourceOutcome, ...]:
    """Rebuild SourceOutcome records from their stored dict form, ignoring any
    field a future version added that this one does not know about."""
    if not isinstance(raw, list):
        return ()
    fields = {"name", "status", "count", "ms", "error"}
    out = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            out.append(SourceOutcome(**{k: v for k, v in item.items() if k in fields}))
    return tuple(out)


async def _query_embedding(query: str) -> list | None:
    """Embedding for *query*, or None when embeddings are unavailable.

    Persisted by canonical query key (1.3). Query embeddings are the ones a
    restart hurts most: the semantic cache cannot answer a single search until
    the typed query has been embedded again, so a cold worker pays for an
    embedding before it can even discover it already has the results.
    """
    from ai.llm_provider import EMBEDDING_MODEL

    store_key = shared_store.embedding_key(
        f"query:{canonical_key(query)}", EMBEDDING_MODEL, "RETRIEVAL_QUERY"
    )
    cached = _embedding_cache.get(store_key)
    if cached and time.time() < cached[1]:
        return cached[0]

    stored = await shared_store.get_embeddings([store_key])
    vector = stored.get(store_key)
    if vector:
        _embedding_cache[store_key] = (vector, time.time() + 600)
        return vector

    try:
        from ai.llm_provider import get_embedding
        vector = await get_embedding(query, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        logger.warning(f"Query embedding failed, semantic cache disabled for this search: {e}")
        return None

    if vector:
        _embedding_cache[store_key] = (vector, time.time() + 600)
        await shared_store.put_embeddings({store_key: vector})
    return vector


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


# How the fan-out stops waiting. Once this many sources have answered, the rest
# get a short grace period and are then cancelled — the aggregate ceiling stays
# as the backstop, but it is no longer what every search actually costs.
#
# The case this fixes: Semantic Scholar's tail latency. Eight sources answer in
# two seconds, one takes nineteen, and every user waits for the nineteen because
# the only stopping condition was the 20s ceiling. Its papers are almost always
# duplicates of what the other eight already returned (dedupe merges them), so
# the wait bought a handful of merged fields at ten times the latency.
HEDGE_QUORUM = 5
HEDGE_GRACE_SECONDS = 3.0


async def _wait_with_grace(tasks: set, ceiling: float) -> tuple[set, set]:
    """Wait for *tasks*, giving up on stragglers once a quorum has answered.

    Returns ``(done, pending)`` exactly as ``asyncio.wait`` does, so the caller's
    timeout/harvest handling is unchanged.
    """
    if not tasks:
        return set(), set()

    quorum = min(HEDGE_QUORUM, len(tasks))
    deadline = time.monotonic() + ceiling
    done: set = set()
    pending: set = set(tasks)

    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Before the quorum, wait out the whole budget. After it, the grace
        # period is the real deadline.
        slice_timeout = remaining if len(done) < quorum else min(remaining, HEDGE_GRACE_SECONDS)
        finished, pending = await asyncio.wait(
            pending, timeout=slice_timeout, return_when=asyncio.FIRST_COMPLETED
        )
        done |= finished
        if not finished:
            # The slice expired with nothing new — either the ceiling or the
            # grace period, and both mean stop.
            break
        if len(done) >= quorum and pending:
            # Quorum reached on this pass: one more short window for anything
            # about to land, then the caller cancels the rest.
            finished, pending = await asyncio.wait(
                pending,
                timeout=min(max(0.0, deadline - time.monotonic()), HEDGE_GRACE_SECONDS),
            )
            done |= finished
            break

    return done, pending


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

    # Sources come from `integrations.registry` — one list, which is also the
    # order per-database yield is reported in. The callable is resolved through
    # this module's namespace, so patching e.g. `paper_search.arxiv_search`
    # still works.
    circuit_open: dict[str, str] = {}
    named: list[tuple[str, asyncio.Task]] = []
    for source in registry.all_sources():
        if source.name in exclude_sources:
            skipped.append(source.name)
            continue
        # A source that has been failing for the last few minutes costs this
        # search its whole per-source timeout and returns nothing. Skip it until
        # the breaker lets a probe through.
        reason = api_health.blocked_reason(source.name)
        if reason is not None and not api_health.allow(source.name):
            circuit_open[source.name] = reason
            continue
        named.append((source.name, asyncio.create_task(
            source.call(query, limit_per_source), name=source.name
        )))

    task_to_name = {task: name for name, task in named}
    all_tasks = {task for _, task in named}
    by_name: dict[str, SourceOutcome] = {
        name: SourceOutcome(name=name, status="skipped") for name in skipped
    }
    for name, reason in circuit_open.items():
        by_name[name] = SourceOutcome(name=name, status="skipped", error=reason)
    if circuit_open:
        logger.info("Skipping %d source(s) with an open circuit: %s",
                    len(circuit_open), sorted(circuit_open))

    # Bound aggregate source latency while still returning fast partial results.
    done, pending = await _wait_with_grace(all_tasks, source_timeout)

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
    # Semantic Scholar, Springer, EuropePMC, DOAJ already tag their own in their modules (or we enforce it here if not)
    for p in springer_results:
        p.setdefault("source", "Springer")
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
    await _shared_exact_store(cache_key, unique, sources)
    # Remember the meaning of this query too, so a paraphrase can reuse it.
    if query_embedding:
        await semantic_cache.store_shared(
            bucket_key, cache_key, query_embedding, query, unique, sources=sources
        )
    return unique, sources
