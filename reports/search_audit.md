# Search & Fetch Audit — AI-Assisted Research Paper Guide

**Date:** 2026-08-11
**Scope:** Every search / content-fetch path across all sections — backend fan-out, ranking, relevance filtering, caching, and all frontend fetch call sites.
**Lens:** Ambiguity, edge cases, semantic caching, optimization.
**Basis:** Live working tree (uncommitted state), not commit blobs.

Related: [`code-report.md`](code-report.md) (full codebase audit), [`audit-checklist.md`](audit-checklist.md).

---

## 1. Map — every fetch path

| Entry | Path | Cost per call |
|---|---|---|
| `/api/topics` | `discover_topics` → `search_all(intent, 20)` → drop BASE/DOAJ → head 60 → TF-IDF | 11 sources, no LLM |
| `/api/literature` | `search_all(query, 20)` → `[:limit]` → `_filter_relevant_papers` | 11 sources + **1 LLM call per paper** |
| `/api/manuscript` | `search_all(topic, 15)[:15]` → filter | 11 sources + 15 LLM |
| gap analysis | `search_all(topic, 15)[:15]` → filter | 11 sources + 15 LLM |
| `/api/arxiv/feed`, `/api/arxiv/search`, `/api/arxiv/trending` | direct arXiv — no cache, no validation, no rate limit | 1–6 HTTP |
| `/api/crossref-journals`, `/api/github/*` | direct — no validation, no rate limit | 1 HTTP |
| `/api/sources` (`routers/pdf.py:108`) | Mongo | cheap |

### Caches present today

All in-memory, all per-process, none evicted.

| Cache | Key | TTL | Location |
|---|---|---|---|
| `paper_search._cache` | `f"{query}_{limit}_{diversify}_{semantic_rerank}_all"` | 600 s | `paper_search.py:270` |
| `paper_search._embedding_cache` | `hash(text)` | 600 s / 60 s on failure | `paper_search.py:57` |
| `paper_search._inflight` | same as `_cache` — single-flight, correct | — | `paper_search.py:284` |
| `relevance._relevance_cache` | `(topic.lower(), title[:60])` | 600 s | `relevance.py:43` |
| `llm_provider._gemini_caches` | context cache, ≥32k tokens only | 1800 s | `llm_provider.py:36` |
| frontend | `sessionStorage dash_*` + `dash_cache_ver` | session | `Dashboard.jsx:78-106` |

---

## 2. Defects

### 2.1 Ambiguity — same intent, different work and different results

**A1 — Cache key is the raw query string.**
`paper_search.py:270`. `"Machine Learning"`, `"machine learning"`, and `"machine  learning"` are three distinct entries and three full 11-source fan-outs. `relevance.py:44` lowercases its own key; `search_all` does not. The two caches disagree on identity.

**A2 — Dashboard → Literature Survey navigation always misses the cache.**
`keyword_extractor.py:211` emits `term.title()` (Title Case) as the direction label. Clicking a direction navigates with that label as the query, but the corpus was built from the lowercase original. Guaranteed cold fan-out plus full LLM classification for a result set computed seconds earlier.

**A3 — `_deduplicate` drops real papers and keeps real duplicates.**
`paper_search.py:102-139`:
- A paper with a **new** DOI but an already-seen title is dropped (line 130), even when genuinely distinct.
- Title is truncated to 60 chars, so `"…Medical Image Segmentation Part I"` and `"Part II"` collapse into one.
- Empty-title papers: the first inserts `""` into `seen_titles`, so every later untitled paper is discarded regardless of DOI.
- No fuzzy matching — an arXiv preprint and its published version survive as two rows.

**A4 — `_normalize_title` leaves collapsed-punctuation whitespace runs.**
`paper_search.py:78-81`. `re.sub(r"[^a-z0-9 ]", "", …)` maps `"deep — learning"` to `"deep  learning"` (two spaces), which never equals `"deep learning"`. Duplicate leak.

**A5 — Relevance verdict keyed on a title prefix.**
`relevance.py:43`. Two different papers sharing a 60-char title prefix share one yes/no verdict.

**A6 — Ranking inversion at the rerank window boundary.**
`paper_search.py:426-440`. Papers 1–30 get `_semantic_rank = 0.6·lexical + 0.4·semantic`. Papers 31+ get `_semantic_rank = lexical`, **unscaled**. Both populations are then sorted together. A rank-31 paper at lexical 0.70 outranks a rank-5 paper whose blended score is `0.6·0.70 + 0.4·0.50 = 0.62`. Two scales, one sort.

**A7 — Filter runs after windowing; `has_more` ignores the filter.**
`routers/discovery.py:60-72`. `limit=15` can return 6 papers, and `has_more = total > len(window)` is computed from the pre-filter count. No backfill of dropped papers.

**A8 — Guardrail and rate limit are applied to opposite halves of the same pair.**
`/api/topics` validates (`topic_discovery.py:29`) but carries **no** `@limiter`. `/api/literature` carries `5/minute` (`discovery.py:44`) but **no** validation. The Dashboard fires both simultaneously for the same query — the rate limit trips on one while the other keeps burning source quota. `/api/arxiv/*`, `/api/github/search`, and `/api/crossref-journals` have neither.

**A9 — Fail-open never caches the failure.**
`relevance.py:140-142`. When Groq is rate-limited every paper is included *and* every paper is retried on the next identical request. The failure mode amplifies load instead of shedding it.

**A10 — `_CURRENT_YEAR` is frozen at import.**
`paper_search.py:31`. Recency scoring drifts after New Year on a long-lived process.

**A11 — `hash()` used as an embedding cache key.**
`paper_search.py:57`. `str.__hash__` is randomized per process via `PYTHONHASHSEED`, so the cache can never be persisted or shared across workers, and collisions silently return the wrong vector.

**A12 — `diversify` is dead weight.**
Never passed as `True` anywhere, yet still part of the cache key. `_apply_diversity_quota` hardcodes 15/9 regardless of the requested limit.

### 2.2 Frontend

**A13 — `SourcesPanel` refetches on every `topic` change with no debounce and no abort.**
`SourcesPanel.jsx:36-38`. Out-of-order responses overwrite each other. Only Dashboard, LiteratureSurvey, and ManuscriptBuilder use `AbortController` at all (9 occurrences across 3 of 7 fetching files).

**A14 — Autocomplete is `String.includes` over a 16-item hardcoded array.**
`Dashboard.jsx:149`. `"ML"` returns 0 hits. `"CNN"` returns 0 hits.

**A15 — `loadMore` refetches the whole window and `setPapers` replaces the list**, resetting filters and scroll position.

### 2.3 Optimization

**C1 — A new `httpx.AsyncClient` per source per search.**
`openalex.py:33` and the same pattern in all 11 integrations. Eleven TCP + TLS handshakes on every search. A shared pooled client saves roughly 100–300 ms per source.

**C2 — Relevance classification dominates latency.**
`limit=100` issues 100 calls through `global_llm_sem = asyncio.Semaphore(3)` (`llm_provider.py:20`) — about 34 serial rounds. This is the single largest cost in the application.

**C3 — Fetch 220, show 6.**
The Dashboard's `/api/literature?limit=6` still fans out 11 sources × `SHARED_LIMIT_PER_SOURCE=20`.

**C4 — arXiv category feed is uncached.**
Every click in the Dashboard left rail triggers a fresh RSS fetch.

**C5 — Unbounded cache growth.**
`_cache`, `_embedding_cache`, and `_relevance_cache` are never evicted. Embeddings are 3072-dim float lists, 30 per search, held for the process lifetime.

**C6 — All caches are per-process.**
N uvicorn workers means N× fan-out, N× embedding spend, and inconsistent results between two requests from the same user.

---

## 3. Fix plan

### Phase 1 — Canonical query key
*Fixes A1, A2. Prerequisite for Phase 2.*

Add `backend/core/query_key.py` exposing `canonical(q)`: NFKC normalize → casefold → collapse whitespace → strip punctuation → sorted content-stem set, reusing `keyword_extractor._stemish` and `GRAMMAR_STOPS`. Return both `display` (trimmed original) and `key` (canonical form).

Then:
- `search_all` keys on `canonical(query)`.
- `relevance._cache_key` uses the same function.
- `keyword_extractor.extract_top_topics` returns `{title, query}` so a direction click sends the original query, not the Title-Cased label.
- Frontend `normalizeSearchQuery` is extended to match, so the client-side dedupe guard and the server cache agree on identity.

**Effect:** `"Machine Learning"`, `"machine learning"`, and a Dashboard direction click all resolve to one cache entry.

### Phase 2 — Semantic cache
*The core ask.*

The query embedding is already computed at `paper_search.py:423` and then discarded. Keep it.

New `backend/services/semantic_cache.py`:
- Store `(canonical_key, query_embedding, results, ts)`.
- Lookup order: exact canonical hit → return. Otherwise cosine similarity against stored query embeddings.
- **≥ 0.94** serves the cached result, tagged `cache: "semantic"` with `matched_query` so the UI can state which query it actually answered.
- Two tiers: 0.94–0.97 serves the cached *papers* but re-runs ranking against the new query (cheap, no network). ≥ 0.97 serves verbatim.
- Same structure for the relevance verdict cache: key on `(query_embedding_bucket, doi | title_hash)` instead of `(topic_string, title[:60])`, so `"CNN classification"` reuses verdicts computed for `"convolutional neural network classification"`.

The threshold must be measured, not guessed. Phase 2 ships with `backend/scripts/eval_semantic_cache.py`, which replays a fixed query list and reports the false-hit rate.

### Phase 3 — Correctness

- Rewrite `_deduplicate`: match on DOI → arXiv ID → normalized title, in that order. Normalize whitespace runs (A4). Drop the 60-char truncation in favour of full normalized title + year. On collision keep the higher-cited record. Add fixtures for preprint/published pairs and empty titles (A3).
- Fix the rerank scale (A6): either rerank the entire returned window, or apply `0.6·lexical + 0.4·semantic` to out-of-window papers with `semantic = 0`. One scale, one sort.
- Filter-then-window with backfill (A7): classify in ranked order, stop once `limit` relevant papers are collected, and report `has_more` as "unclassified papers remain".
- Negative-cache classifier failures with a short TTL (A9).
- `_CURRENT_YEAR` becomes a function call (A10). `hash()` becomes a `hashlib.blake2b` digest (A11).
- Add `validate_input_layers_a_b` to `/api/literature`, `/api/arxiv/search`, and `/api/github/search`; add `@limiter` to `/api/topics` (A8).

### Phase 4 — Optimization

- Shared pooled `httpx.AsyncClient` in `integrations/base_client.py`, one per process, `limits=Limits(max_keepalive_connections=20)` (C1).
- **Batch relevance classification** — 10 papers per prompt, numbered, expecting `1:yes 2:no …`. Turns 100 calls into 10 (C2). Largest single latency win. Falls back to per-paper classification on a malformed reply.
- Scale `limit_per_source` to the requested limit instead of a fixed 20 (C3).
- TTL-cache the arXiv category feed at 15 minutes (C4).
- Bound every cache with `cachetools.TTLCache(maxsize=…)`; cap embeddings by entry count (C5).
- Put caches behind a small interface so Redis can back them once workers > 1 (C6). Interface now, Redis when needed.

### Phase 5 — Frontend

- Extract a `useSearchRequest` hook holding the `isCurrent` / abort pattern currently duplicated across Dashboard and LiteratureSurvey; adopt it in `SourcesPanel` (A13) and `ManuscriptBuilder`.
- `loadMore` appends by `paperKey` instead of replacing the list (A15).
- Autocomplete: prefix + acronym matching, backed by a `/api/suggest` endpoint over cached queries rather than the 16-item array (A14).

---

## 4. Sequencing and risk

Run **Phase 1 → 3 → 2 → 4 → 5**.

Canonical keys and dedupe correctness must land before semantic caching, otherwise the semantic cache memoizes wrong result sets and every later measurement is taken against a corrupted baseline. Phase 2 precedes Phase 4 so that batching is measured against a stable cache hit rate.

Two changes are user-visible and should not ship silently:
- Phase 3 alters ranking output — expect noticeable reordering of results.
- Phase 2 can serve results for a query the user did not type. The `matched_query` tag in the response is not optional.

---

## 5. Status

Analysis only. No Phase 1–5 work implemented as of 2026-08-11.

Already fixed separately (not part of the phases above):
- Layer A gibberish heuristic rewritten to score per word token instead of the whitespace-stripped string, in both `backend/ai/guardrails.py` and `frontend/src/utils/searchHeuristics.js`. Acronym queries (`"CNN classification"`, `"NLP transformers"`, `"LLM"`, `"TCP throughput"`) were being rejected as keyboard mash.
- Aborted-request state clobbering in `Dashboard.jsx` and `LiteratureSurvey.jsx`: a superseded run's `catch`/`finally` now no-ops via an `isCurrent()` guard, and `stopDiscover`/`stopSearch` own the "Search stopped." message.
