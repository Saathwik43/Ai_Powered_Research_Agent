# Codebase Audit — AI-Assisted Research Paper Guide

**Date:** 2026-08-08
**Scope:** Full backend (`backend/`), frontend (`frontend/src/`), CI, and deployment posture.
**Lens:** Security, performance/bottlenecks, product gaps, and the path to producing genuinely publishable research output.

---

## 0. Starting position

Credit where due before the criticism. The following are genuinely above typical project quality and form a solid base:

- Multi-provider LLM cascade with **mid-stream continuation** on provider failure (`ai/llm_provider.py:642`).
- **Tiered evidence extraction**: arXiv LaTeX source → GROBID on OA PDF → LLM on title/abstract (`ai/evidence_extraction.py:166`).
- A dedicated **citation-grounding** module that narrows evidence to only the papers actually cited per sentence (`ai/citation_grounding.py`).
- **SSRF guard** with private/loopback/link-local rejection and `follow_redirects=False` (`ssrf_guard.py`).
- **Magic-byte upload validation** rather than trusting extensions (`file_validation.py`).
- JWT **revocation list** with Mongo TTL expiry (`auth.py:80`).
- Security headers middleware (`main.py:102`).
- Live third-party **health checks** across 23 integrations (`admin_status.py`).
- 11-source federated search with dedupe + hybrid lexical/semantic ranking (`integrations/paper_search.py`).
- CI running backend tests + frontend lint/build (`.github/workflows/ci.yml`).
- Secrets are correctly **not** tracked in git (verified via `git ls-files`).

The issues below are the difference between "impressive project" and "product." They are not signs of sloppiness.

---

# 1. Security Vulnerabilities

## 1.1 CRITICAL — Cross-user data leak via the research cache

**Location:** `ai/manuscript_generation.py:154` vs `:163-175`

`_research_cache[cache_key] = (papers, now)` stores a **reference** to the `papers` list. Immediately afterwards, the user's private uploaded sources are appended to that same list object. The cache key is the **topic alone** — there is no user scoping.

Consequences:
1. User A uploads confidential unpublished data → it is appended into the shared cache entry.
2. User B generates on the same topic → receives A's raw source text in their prompt context and reference list.
3. On every cache hit the same list is re-appended to, so it grows without bound.

**This is the single most serious issue in the codebase.**

**Fix:** Deep-copy before caching; cache only the public search/evidence results; merge user-private sources *after* the cache read, never into it. Scope any cache containing user content by `user_id`.

---

## 1.2 CRITICAL — Password-reset tokens function as full access tokens

**Location:** `auth.py:64` (`decode_access_token`) and `auth.py:101` (`get_current_user`)

`decode_access_token()` verifies signature and expiry but **never checks the `purpose` claim**. `create_reset_token()` (`auth.py:51`) signs with the same secret and the same algorithm, and its payload carries `sub`.

Therefore a password-reset token, presented as `Authorization: Bearer <reset_token>`, authenticates successfully and grants full API access for its 30-minute lifetime.

Aggravating factors:
- Reset tokens travel through email and appear in URLs.
- `GET /api/auth/validate-reset-token?token=…` (`main.py:250`) places one in a **query string**, which lands in web-server access logs, proxy logs, and browser history.

**Fix:** Assert `purpose` in `decode_access_token` (reject anything that isn't an access token). Use a separate signing key or key derivation for reset tokens. Move the validate endpoint to POST with a body.

---

## 1.3 CRITICAL — Cross-user PDF leak via client-supplied `chat_id`

**Location:** `main.py:709` (form field) → `ai/pdf_analysis.py:334` (`get_or_create_gemini_cache`)

`chat_id` arrives as an **unvalidated form field** and is used directly as:
- the Gemini context cache key: `f"pdf:{chat_id}"`
- the `_rolling_summaries` dict key (`ai/pdf_analysis.py:234`)

`get_or_create_gemini_cache` returns any existing cache registered under that key. A user supplying another user's `chat_id` receives answers generated against **that user's PDF content**. MongoDB ObjectIds are partly timestamp-derived and therefore partially guessable.

**Fix:** Derive all cache keys server-side from `(authenticated_user_id, chat_id)` after verifying ownership of the chat document.

---

## 1.4 CRITICAL — Account pre-hijacking (no email verification)

**Location:** `auth.py:136` (`signup_user`), `auth.py:222` (`google_auth_user`), `main.py:120` (`SignupPayload`)

Signup accepts any email address with **no verification step**. Google sign-in matches purely on email address and logs the user into the pre-existing account.

Attack: register `victim@university.edu` with a password → victim later signs in with Google → they are placed into the attacker's account → attacker retains password access to the victim's manuscripts and uploaded data.

Additionally, `SignupPayload.email` is a bare `str` with `min_length=1`, not `EmailStr` — so the address isn't even format-validated.

**Fix:** Mandatory email verification before account activation. Use `pydantic.EmailStr`. On Google sign-in, if a password-auth account with that email exists and is unverified, require verification before linking.

---

## 1.5 HIGH — Unmetered, cost-amplifying endpoints

The following carry **no `@limiter.limit` decorator**:

| Endpoint | File:Line | Cost per call |
|---|---|---|
| `/api/topics` | `main.py:262` | LLM call |
| `/api/venues` | `main.py:722` | LLM call |
| `/api/guidelines` | `main.py:742` | LLM call |
| `/api/arxiv/search` `/feed` `/trending` | `main.py:287-319` | up to 6 upstream fetches |
| `/api/github/sync` | `main.py:348` | `git clone` subprocess |
| `/api/github/search` `/categories` `/papers` | `main.py:366-389` | filesystem walk |
| `/api/crossref-journals` | `main.py:324` | upstream fetch |
| `/api/literature/save` `/load` `/list` `/delete` | `main.py:752-790` | DB write |
| **All** `/api/admin/*` | `main.py:875-1048` | heavy aggregations |

Any authenticated account can drain the provider budget in minutes.

**Fix:** Default-deny — apply a global limiter and override per-endpoint, rather than opting in.

---

## 1.6 HIGH — Quota is bypassable on the most expensive path

**Location:** `main.py:418`, `ai/manuscript_generation.py:331-342`

- `/api/manuscript/stream` **explicitly skips `check_quota`** (comment: "No usage_tracker.check_quota here per user request").
- Usage is logged **only when a `done` event fires**. A client that disconnects mid-stream, or a cascade ending in `stopped`, bills **zero tokens**.
- The amount billed is `word_count * 1.3` — an estimate over prompt + output, not the provider's reported usage.

**Fix:** Enforce quota before the stream opens and debit incrementally. Capture real usage from `stream_options: {include_usage: true}` (OpenAI-compatible) and Gemini's `usage_metadata`.

---

## 1.7 HIGH — No model allowlist

**Location:** `main.py:420-421` → `ai/llm_provider.py:345`

`provider` and `model` come straight from the request body into `current_provider.set()` / `current_model.set()` and through to the outbound provider payload. A user can name an arbitrarily expensive model on your OpenRouter/OpenAI key.

**Fix:** Server-side allowlist of `(provider, model)` pairs, with per-tier entitlements.

---

## 1.8 HIGH — Prompt injection is structurally unaddressed

**Location:** `ai/guardrails.py`; ingestion at `ai/manuscript_generation.py:180-203`

`validate_input_layers_a_b` is a six-pattern regex blocklist. Meanwhile the system concatenates **untrusted third-party text** directly into prompts:

- Abstracts from 11 external APIs
- Arbitrary user-supplied URLs (`/api/sources/upload`)
- Full PDF text via GROBID / LlamaParse
- GitHub markdown from cloned repos

A poisoned abstract or attacker-hosted page can steer generation, fabricate citations, or extract the system prompt.

**The blocklist is also actively harmful to legitimate users.** `validate_layer_b` rejects any PDF containing "bypass" or "exec(" (`ai/pdf_analysis.py:128`). That breaks:
- Cardiology — "coronary artery bypass grafting"
- Security — "CAPTCHA bypass detection", "sandbox bypass"

Two of the largest addressable markets, blocked, while stopping no real attacker.

**Fix:** Remove the blocklist. Replace with structural defence: delimit untrusted content explicitly, instruct the model that delimited content is data not instructions, and validate *outputs* (schema conformance, citation-index validity) rather than filtering inputs.

---

## 1.9 HIGH — Unbounded uploads and fetches

**Location:** `main.py:638`, `main.py:620-623`

- `/api/sources/upload` file branch: `raw = await file.read()` with **no size check whatsoever** — unlike `/api/manuscript/extract-pdf`, which caps at 10 MB (`main.py:589`).
- URL branch: `resp.text` read with no size cap and no content-type check; 10 s timeout only.

Either is a trivial memory-exhaustion DoS.

**Fix:** Streaming size cap on both paths, content-type allowlist on the URL path, and a request-body-size middleware as a backstop.

---

## 1.10 HIGH — SSRF guard has a DNS-rebinding window

**Location:** `ssrf_guard.py:23-38` → `main.py:620`

`assert_public_url` resolves the hostname, then `httpx` resolves it again independently. Between the two lookups an attacker-controlled DNS record can flip to an internal address (TOCTOU / DNS rebinding).

`follow_redirects=False, max_redirects=0` is correct and good. Missing: port restriction, response-size ceiling, content-type check.

**Fix:** Resolve once, validate, then connect to the **pinned IP** with the original Host header. Add a port allowlist (80/443) and a body-size ceiling.

---

## 1.11 MEDIUM — Mermaid XSS chain

**Location:** `frontend/src/components/Mermaid.jsx:26`, `:146`, `:181`, `:206`

- `securityLevel: 'loose'` enables `click` directives and HTML labels.
- Rendered SVG is inserted via `dangerouslySetInnerHTML` and raw `innerHTML`.
- The diagram source is **LLM-generated** from text that is influenceable by third parties (see 1.8).

Combined with:
- Tokens in `sessionStorage` (`frontend/src/context/AuthContext.jsx:11-22`) — readable by any script.
- **No `Content-Security-Policy`** header (`main.py:102-110` sets five headers; CSP is absent).

That is a complete session-theft chain.

**Fix:** `securityLevel: 'strict'`, sanitise rendered SVG with DOMPurify before insertion, add a strict CSP.

---

## 1.12 MEDIUM — Rate-limit key is proxy-dependent

**Location:** `main.py:53-64`

`get_user_id_for_rate_limit` falls back to `get_remote_address`, which reads `request.client.host`.

- If uvicorn's `forwarded_allow_ips` is **not** configured behind Render's proxy → every anonymous user shares a single bucket, so the 5/min login limit is trivially DoS'd for everyone.
- If it trusts `X-Forwarded-For` **unconditionally** → the limit is trivially spoofed.

Both outcomes are bad. This needs a deliberate trusted-proxy configuration.

---

## 1.13 MEDIUM — CORS configuration

**Location:** `main.py:80-97`

`allow_credentials=True` with `allow_methods=["*"]` and `allow_headers=["*"]`, over origins split from an env var with no trimming or validation. A trailing space or stray wildcard silently breaks the policy.

Since authentication is a Bearer header (not a cookie), `allow_credentials=True` is **not needed at all**.

---

## 1.14 MEDIUM — Incomplete user deletion

**Location:** `main.py:1029-1036`

`admin_delete_user` removes `users`, `usage_logs`, `manuscripts`, `pdf_chats` — but leaves orphaned:
- `sources` (user-uploaded private research data)
- `literature` (saved surveys)
- GridFS `pdfs` bucket (uploaded PDF binaries)

Both a data leak and a GDPR right-to-erasure failure.

---

## 1.15 MEDIUM — Sensitive data in logs

**Location:** `main.py:50`, `ai/pdf_analysis.py:170`

- `logging.basicConfig(filename="backend.log")` — no rotation, writes to CWD, unbounded disk growth.
- `logger.info(f"Extracted PDF Structure: {json.dumps(structure, indent=2)}")` writes the **full extracted structure of every user PDF** in plaintext.

---

## 1.16 MEDIUM — Undisclosed third-party data processors

- `llama-parse` ships user PDFs to **LlamaCloud** (`ai/pdf_analysis.py:83`).
- GROBID runs on a **public HuggingFace Space** (`ai/grobid_client.py`, referenced `admin_status.py:406`).

Neither is disclosed to the user. For a product handling unpublished research this requires an explicit consent surface and a data-processing agreement.

---

## 1.17 MEDIUM — Other findings

| Issue | Location | Note |
|---|---|---|
| Raw exception strings in API responses | `admin_status.py` (`str(e)` throughout) | Can surface upstream URLs/params |
| `python-jose` 3.5.0 | `requirements.txt:50` | Weak CVE history, effectively unmaintained — migrate to PyJWT |
| No token refresh/rotation | `auth.py:27` | 24 h non-rotating access tokens |
| No `aud` / `iss` verification | `auth.py:66` | JWT claims unvalidated |
| No password complexity policy | `main.py:122` | `min_length=8` only |
| `grid_out.metadata` may be `None` | `main.py:689` | `AttributeError` → 500 |
| No `TrustedHostMiddleware` / HTTPS redirect | `main.py` | Host-header attacks |
| Google `client_secret_*.json` on disk | `backend/integrations/` | Gitignored, but shouldn't be in the tree |
| `email_verified` not checked | `auth.py:216` | Google token claims partially validated |

---

# 2. Bottlenecks & Performance

## 2.1 Time-to-first-token — the product's biggest operational flaw

**Location:** `ai/manuscript_generation.py:127-254`

On a cache miss, `_prepare_generation` runs **entirely in the request path** before a single character streams:

1. 11 source APIs in parallel — 20 s ceiling (`integrations/paper_search.py:255`)
2. Up to 15 relevance LLM calls at semaphore 3 → ~5 serial rounds
3. Up to 15 evidence extractions, each potentially downloading a PDF **and** calling GROBID, at semaphore 3
4. Gap analysis (literature-review sections only) — another full LLM round with retry
5. Optional Gemini cache creation

Realistically **60–180 seconds of blank screen**. Even the `sources_list` SSE event is only emitted afterwards (`:315`).

**Fix:** Convert to a background job against a persisted project corpus, with a granular progress stream. Generation should start from already-prepared context.

---

## 2.2 `/api/literature` is pathological

**Location:** `main.py:279-282` → `ai/relevance.py:73`

`search_all(query, limit_per_source=20)` across 11 sources yields up to 220 papers. `_filter_relevant_papers` then makes **one LLM call per paper** through a semaphore of 3 — roughly **70 serial rounds** for a single search, behind a 5/min rate limit.

**Fix:** Embedding similarity for the bulk filter (you already have `get_embedding` at `ai/llm_provider.py:322`); reserve LLM classification for a narrow borderline band.

---

## 2.3 Every cache is process-local and volatile

| Cache | Location | Problem |
|---|---|---|
| `_research_cache` | `manuscript_generation.py:124` | Unbounded, unscoped, lost on redeploy |
| `_relevance_cache` | `relevance.py:37` | Unbounded, 600 s TTL |
| `_evidence_cache` | `evidence_extraction.py:38` | Unbounded, 600 s TTL |
| `_cache` (search) | `paper_search.py:18` | Unbounded |
| `_embedding_cache` | `paper_search.py:19` | Keyed by `hash()` — **randomized per process**; 600 s TTL on immutable data |
| `_gemini_caches` | `llm_provider.py:31` | Unbounded |
| `_rolling_summaries` | `pdf_analysis.py:174` | Unbounded, client-keyed (see 1.3) |
| `_STATUS_CACHE` | `admin_status.py:504` | Fine, but per-process |
| `svgCache` | `Mermaid.jsx:9` | Unbounded client-side |

None are shared across workers; all are lost on every redeploy. Embeddings in particular are immutable and expensive — they belong in MongoDB keyed by DOI, permanently.

---

## 2.4 No HTTP connection reuse

**Location:** `llm_provider.py:154, 179, 204, 229, 254, 285`; `admin_status.py:74`; integration modules

Every provider call constructs a fresh `httpx.AsyncClient` — a new TLS handshake each time. One manuscript generation triggers 30+ handshakes.

**Fix:** Module-level shared `AsyncClient` with connection limits and keepalive, created on app lifespan startup.

---

## 2.5 Two DB round-trips on every authenticated request

**Location:** `auth.py:107` (`is_token_revoked` → `find_one`) and `auth.py:120` (`users.find_one`)

This runs on the hot path for every single endpoint.

**Fix:** Short-TTL Redis cache for the user document and revocation set, or embed `role`/`status` in a short-lived JWT with refresh-token rotation.

---

## 2.6 Quota checks run an unindexed aggregation

**Location:** `usage_tracker.py:34-41`; `database.py:28-36`

`check_quota` runs `$match` + `$group` over `usage_logs` on every LLM call. `ensure_indexes` creates **no index on `usage_logs` at all**. The collection grows one document per LLM call, forever.

**Fix:** Compound index on `(user_id, date)`, plus a daily rollup counter document so the hot path is a single `find_one`.

---

## 2.7 Admin dashboard is N+1

**Location:** `main.py:930-943`

`/api/admin/users` runs **two aggregations per user** inside a Python loop over `to_list(1000)`. Will collapse at a few hundred accounts.

**Fix:** Single `$group` aggregation joined in memory, or `$lookup`.

---

## 2.8 Thread-pool leak on HuggingFace

**Location:** `llm_provider.py:92`, `:317`, `:378`

`_generate_huggingface` runs a blocking call in a 4-worker `ThreadPoolExecutor`. `asyncio.wait_for(..., timeout=60)` cancels the *await* but the underlying thread keeps running. Four stuck calls permanently wedge the HuggingFace path.

---

## 2.9 Broken fallback semaphore

**Location:** `llm_provider.py:417`

```
sem = provider_semaphores.get(provider_name, asyncio.Semaphore(3))
```

The default constructs a **new semaphore on every call** — zero actual throttling for unlisted providers. Additionally, module-level `asyncio.Semaphore` objects bind to the event loop that creates them, which breaks under multiple workers and in tests.

---

## 2.10 No disconnect detection on SSE

**Location:** `main.py:410-412` (`_sse_wrap`)

Never checks `await request.is_disconnected()`. A closed browser tab keeps the upstream LLM stream — and its cost — running to completion.

---

## 2.11 Cascade continuation is quadratic

**Location:** `llm_provider.py:654`

`stream_completion_auto` re-sends the **entire accumulated draft** to each subsequent provider on fallback. Token cost grows quadratically across the chain, and the joins produce visible stylistic seams.

---

## 2.12 Frontend

- No route-level code splitting. `mermaid` + `katex` + `pdfjs-dist` + `recharts` + `jspdf` + `react-pdf` all in one bundle.
- `ManuscriptBuilder.jsx` — 1454 lines; `PdfAnalysis.jsx` — 877; `AdminDashboard.jsx` — 698.
- `sessionStorage` used as an ad-hoc data cache for full paper arrays (`Dashboard.jsx:97-111`) — will hit the ~5 MB quota and throw.
- The API base URL is re-derived inline in ~30 places instead of one client module.
- No data-fetching library (React Query / SWR); every page hand-rolls fetch + loading + error state.

---

## 2.13 Manuscript saves destroy history

**Location:** `main.py:447-471`; `database.py:29`

- `save_manuscript_draft` overwrites `content` wholesale — **no version history**. One bad save loses the work.
- The `(user_id, topic)` index is **not unique**, while the code uses find-then-insert → races into duplicate documents.

---

## 2.14 No pagination

`/api/literature` always returns `has_more: false` (`main.py:282`) and ships the full filtered set. Same for `/api/manuscript/list`, `/api/literature/list`, `/api/pdf-chats/list`.

---

# 3. The Core Gap: plausible prose vs. real research

This is the section that matters most against the stated goal — *"produce as real research content as possible"*.

## 3.1 The system is explicitly instructed to fabricate data

**Location:** `ai/manuscript_generation.py:68-88`, and `:36`

Prompt rule #11 instructs the model to render "quantitative data, benchmarks, trends, or distributions" as Mermaid charts, and supplies a **fully invented example bar chart** (`bar [65.4, 78.2, 86.5, 92.8]`). Combined with the results framing at `:36` ("These are PROJECTED/EXPECTED outcomes"), the system produces manuscripts containing **invented benchmark figures**.

If a user submits that, it is research misconduct — generated by your product.

**This is the single biggest credibility liability in the codebase.**

**Fix:** Charts may only be constructed from **extracted** numbers with per-datapoint provenance (paper index + evidence field), or must be strictly non-numeric schematics (architecture/pipeline flowcharts). Never numeric arrays invented by the model.

---

## 3.2 There is no corpus

Every action re-searches the internet from scratch. There is no **Project** entity accumulating a screened paper set, extracted evidence, decisions, and notes. Research is cumulative; the application is stateless per prompt.

This single architectural change unlocks most of the improvements below.

---

## 3.3 Retrieval is one-shot and shallow

**Location:** `ai/manuscript_generation.py:139`, `integrations/paper_search.py:201`

`search_all(topic)` on the raw topic string. Missing:
- Query expansion / synonym handling
- Controlled-vocabulary mapping (MeSH for biomedical)
- Boolean search strategy construction
- **Citation snowballing** — backward (`referenced_works`) and forward (`cited_by`) chasing from seed papers

OpenAlex and Semantic Scholar both expose citation edges. You integrate both and use neither. Snowballing is how real literature reviews achieve coverage.

---

## 3.4 Full text is rarely actually used

**Location:** `ai/evidence_extraction.py:166-211`

The tiers fall through to **LLM extraction from title + abstract** (`:204`), which is where most non-arXiv papers land. Claims grounded in abstracts are shallow and frequently wrong.

You already have Unpaywall, Europe PMC, CORE, and DOAJ — all of which serve open full text. Chunk it, embed it, ground at the **passage level** with section/page offsets.

---

## 3.5 Grounding is post-hoc auditing, not grounded generation

**Location:** `ai/citation_grounding.py`, `ai/numerical_validator.py`

The model writes freely, then the output is audited. Two consequences:

1. **The user sees the hallucination first.** A warning badge after the fact does not prevent bad content reaching the draft.
2. **The checks are weak.** The "bare number" path (`numerical_validator.py:74`) matches any digit string anywhere in the concatenated abstract blob — a claim of "5%" passes because "5" appears somewhere in the corpus. Effectively random.

Additionally the grounding verdict comes from a **single Groq call** with no schema enforcement (`citation_grounding.py:217-223`), failing closed to `"unverified"`.

**Fix:** Retrieval-anchored generation. Retrieve passages first; force claim-by-claim writing with a mandatory passage id; verify each claim against **its own** passage with an entailment check; **regenerate** the offending sentence rather than flagging it.

---

## 3.6 No experimental-data layer

**Location:** `main.py:606-655`, `ai/manuscript_generation.py:163-175`

This is what separates "AI wrote something paper-shaped" from "AI wrote up my actual research."

`/api/sources/upload` is the seed of the right idea — user data is treated as ground truth in the system prompt (`:235`) — but it is truncated to 4000 characters and stuffed into a prompt string.

A real product needs:
- CSV / XLSX / Parquet upload with schema inference
- A statistics engine — t-tests, ANOVA, effect sizes, confidence intervals, corrections for multiple comparisons
- **Real** figure generation from real data (matplotlib/vega), with the underlying dataset retained
- Auto-generated data-availability statements

---

## 3.7 No novelty / prior-art verification

Nothing checks whether an identified "gap" is genuinely a gap, or whether the proposed contribution is already published.

**Fix:** Embed the proposed contribution; search corpus + OpenAlex for nearest neighbours; report closest prior art with similarity scores.

---

## 3.8 No plagiarism / self-similarity check

Publishers run iThenticate. At minimum, check generated text against your own retrieved corpus for n-gram and embedding overlap, and warn before export.

---

## 3.9 No adversarial review loop

Gap analysis exists, but there is no reviewer agent that scores a draft against a venue's **actual** review criteria (novelty, soundness, reproducibility, clarity), returns actionable revisions, and iterates until it passes. Cheap relative to its quality impact.

---

## 3.10 Bibliography handling is thin

**Location:** `ai/latex_export.py:165-195`, `ai/citation_format.py`

- No CSL support (thousands of styles); only four hardcoded formats.
- No BibTeX / RIS / Zotero / Mendeley import or export.
- No DOI validation against Crossref before export.
- `_build_bibtex` emits `@article` for **everything** — including arXiv preprints (`@misc`) and conference papers (`@inproceedings`).
- Drops `booktitle`, `volume`, `number`, `pages`, `publisher`.
- Citation keys (`_bibtex_key`, `:165`) can collide.

Editors and reviewers notice this immediately.

---

## 3.11 LaTeX export cannot be seen

Per the existing `gap.md`: no class files bundled, no compile step, no PDF preview. Users cannot view what they would submit.

**Fix:** A sandboxed Tectonic / `latexmk` container producing a real camera-ready PDF. This is the feature that makes the export credible. (Verify redistribution terms per venue — IEEEtran, acmart, llncs, and elsarticle are permissively licensed.)

---

## 3.12 Figures and tables are not first-class objects

Real papers have numbered figures/tables, captions, `\ref` cross-references, and lists of figures. Mermaid-in-markdown is a dead end for LaTeX — the exporter can only emit a `% TODO` comment (`latex_export.py:86`).

---

## 3.13 No collaboration, versioning, or provenance

- Single-owner documents keyed `(user_id, topic)`.
- Destructive saves (see 2.13).
- **No record of which model / provider / prompt / temperature produced which section.** `content` is an opaque `Dict[str, Any]` (`main.py:160`).

Provenance is a research-integrity requirement *and* your own debugging story.

---

## 3.14 No compliance / disclosure layer

Missing, and required by most submission portals:
- AI-assistance disclosure (now mandatory at IEEE, ACM, Elsevier, Springer)
- Conflict-of-interest statement
- Data-availability statement
- Ethics / IRB approval reference
- ORCID linkage
- Funding acknowledgment
- License selection (CC-BY variants)

Trivially generated; their absence is what gets a submission bounced.

---

## 3.15 The guardrail rejects your own users

**Location:** `ai/guardrails.py:44`, `:11-18`

- Blocks any input containing **"bypass"** → kills "coronary artery bypass grafting", "CAPTCHA bypass detection", "sandbox bypass".
- Rejects any 5-consonant run → kills "strengths", "TCP/IP", chemical formulas, and many non-English author names.
- Rejects "system prompt", "exec(", "eval(" → kills legitimate CS/security papers.

Medicine and security are two of the largest addressable markets for this product.

---

## 3.16 No literature currency / alerting

The genuine daily pain of a working researcher is *"what's new since last week."* arXiv category feeds are already ingested (`main.py:297-319`) — turn them into per-project saved searches with digest emails via the existing Brevo integration.

---

# 4. Phased Remediation Plan

## Phase 0 — Stop the bleeding (days)

Security and integrity fixes that must ship before any growth.

| # | Item | Ref |
|---|---|---|
| 0.1 | Deep-copy before caching; never merge private user sources into the shared cache | 1.1 |
| 0.2 | Assert `purpose` claim in `decode_access_token`; separate reset-token key; move validate endpoint to POST | 1.2 |
| 0.3 | Derive all cache keys server-side from authenticated user + verified ownership | 1.3 |
| 0.4 | Mandatory email verification; `EmailStr`; safe Google account linking | 1.4 |
| 0.5 | **Remove the fabricated-chart instruction from the generation prompt** | 3.1 |
| 0.6 | Global default rate limit; per-endpoint overrides | 1.5 |
| 0.7 | Enforce quota before stream opens; debit incrementally | 1.6 |
| 0.8 | Server-side `(provider, model)` allowlist | 1.7 |
| 0.9 | Upload size caps on both `/api/sources/upload` branches + body-size middleware | 1.9 |
| 0.10 | `securityLevel: 'strict'` + DOMPurify on Mermaid; add CSP header | 1.11 |
| 0.11 | Compound index on `usage_logs(user_id, date)`; unique index on `manuscripts(user_id, topic)` | 2.6, 2.13 |
| 0.12 | Remove the input blocklist; replace with delimited-content prompting + output validation | 1.8, 3.15 |
| 0.13 | Complete user deletion (sources, literature, GridFS) | 1.14 |
| 0.14 | Stop logging full PDF structures; add log rotation | 1.15 |
| 0.15 | Configure trusted-proxy handling for rate limiting | 1.12 |
| 0.16 | Drop `allow_credentials`; validate/trim CORS origins | 1.13 |

**Exit criteria:** No cross-tenant data path; no unmetered LLM endpoint; no product-generated fabricated figures.

---

## Phase 1 — Make it durable (weeks)

Turn a stateless prompt-runner into a persistent system.

| # | Item | Ref |
|---|---|---|
| 1.1 | **Introduce the Project / Workspace entity** — persisted corpus, evidence, screening decisions, versioned manuscript with full provenance (model, provider, prompt, temperature per section) | 3.2, 3.13 |
| 1.2 | Move every in-process cache to MongoDB / Redis, keyed by content hash and scoped by tenancy | 2.3 |
| 1.3 | Persist embeddings permanently, keyed by DOI / normalized title | 2.3 |
| 1.4 | Shared `httpx.AsyncClient` with keepalive, created on lifespan startup | 2.4 |
| 1.5 | Background job + granular progress stream for the research pipeline | 2.1 |
| 1.6 | Replace bulk LLM relevance filtering with embedding similarity; LLM only for the borderline band | 2.2 |
| 1.7 | Real provider token accounting (`include_usage`, `usage_metadata`) | 1.6 |
| 1.8 | SSE disconnect detection and upstream cancellation | 2.10 |
| 1.9 | Fix thread-pool leak, fallback semaphore, and loop-bound semaphores | 2.8, 2.9 |
| 1.10 | Manuscript version history with restore | 2.13 |
| 1.11 | Cursor pagination on all list endpoints | 2.14 |
| 1.12 | Fix admin N+1; single aggregation | 2.7 |
| 1.13 | Frontend: single API client module, route-level code splitting, React Query, remove `sessionStorage` as a data cache | 2.12 |
| 1.14 | Refresh-token rotation; cache user/revocation lookups | 1.17, 2.5 |
| 1.15 | Migrate `python-jose` → PyJWT | 1.17 |
| 1.16 | Consent surface + DPA for LlamaCloud / GROBID; consider self-hosting GROBID | 1.16 |

**Exit criteria:** Time-to-first-token under 5 s on a warm project; caches survive redeploy; billing reflects real usage.

---

## Phase 2 — Make the output real (weeks → months)

This is where the product's core claim becomes true.

| # | Item | Ref |
|---|---|---|
| 2.1 | **Full-text ingestion** via Unpaywall / Europe PMC / CORE; chunking + passage-level embeddings with section/page offsets | 3.4 |
| 2.2 | **Citation snowballing** — backward/forward chasing via OpenAlex `referenced_works` / `cited_by` and S2 | 3.3 |
| 2.3 | Query expansion, Boolean strategy construction, controlled-vocabulary mapping | 3.3 |
| 2.4 | **Passage-anchored generation** — claim-by-claim with mandatory passage ids, entailment verification, sentence-level regeneration on failure | 3.5 |
| 2.5 | Replace the regex numerical validator with provenance-linked number extraction | 3.5 |
| 2.6 | **PRISMA-style audit trail** — databases queried, dates, query strings, N retrieved / deduped / screened / excluded-with-reason. Both a credibility feature and a strong differentiator; the data is already flowing through the system | 3.2 |
| 2.7 | Provenance-only chart generation — figures built from extracted numbers with per-datapoint citations | 3.1 |
| 2.8 | Real BibTeX with correct entry types and full fields; CSL support; Crossref DOI validation; RIS/Zotero import-export | 3.10 |
| 2.9 | **LaTeX compile service** (sandboxed Tectonic) with true camera-ready PDF preview | 3.11 |
| 2.10 | Figures and tables as first-class entities with captions and `\ref` cross-references | 3.12 |

**Exit criteria:** Every factual sentence in an exported draft traces to a specific passage in a specific retrieved paper, and the user can see the compiled PDF.

---

## Phase 3 — Make it a research platform (months)

| # | Item | Ref |
|---|---|---|
| 3.1 | **Experimental data layer** — dataset upload, statistics engine, real figure generation, data-availability statements | 3.6 |
| 3.2 | Novelty / prior-art check against corpus + OpenAlex | 3.7 |
| 3.3 | Similarity / plagiarism check against the retrieved corpus | 3.8 |
| 3.4 | **Adversarial reviewer loop** scoring against venue-specific review criteria, iterating to convergence | 3.9 |
| 3.5 | Coauthor collaboration — shared projects, comment threads, track changes, roles | 3.13 |
| 3.6 | Compliance & disclosure module — AI disclosure, COI, ethics, ORCID, funding, license | 3.14 |
| 3.7 | Saved-search alerting with digest emails via Brevo | 3.16 |
| 3.8 | Submission-portal integration / export bundles per publisher | — |

---

# 5. Bottom line

The retrieval and grounding infrastructure here is genuinely good and mostly **under-used**. The blockers to producing real research content are not AI-capability problems. They are four structural choices:

1. The system has **no memory** — every action restarts from zero.
2. It **doesn't read full text** — most evidence comes from abstracts.
3. It **audits hallucinations instead of preventing them** — grounding is post-hoc.
4. It is **currently instructed to invent charts** — a direct research-integrity liability.

Fix those four and the quality ceiling rises dramatically without changing a single model or provider.
