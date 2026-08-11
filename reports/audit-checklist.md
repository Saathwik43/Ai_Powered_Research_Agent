# Audit checklist

Local working list from `code-report.md` §4. Not tracked in git.

**Exit (Phase 0):** no cross-tenant data path; no unmetered LLM endpoint; no product-generated fabricated figures.  
**Exit (Phase 1):** TTFT under 5s on a warm project; caches survive redeploy; billing reflects real usage.  
**Exit (Phase 2):** every factual sentence traces to a specific passage; user can see a compiled PDF.  
**Exit (Phase 3):** research-platform features (data, novelty, review, collab, compliance).

---

## Phase 0 — Stop the bleeding

- [ ] **0.1** Deep-copy before caching; never merge private user sources into the shared cache *(1.1)*
- [ ] **0.2** Assert `purpose` in `decode_access_token`; separate reset-token key; move validate endpoint to POST *(1.2)*
- [ ] **0.3** Derive all cache keys server-side from authenticated user + verified ownership *(1.3)*
- [ ] **0.4** Mandatory email verification; `EmailStr`; safe Google account linking *(1.4)*
- [ ] **0.5** Remove the fabricated-chart instruction from the generation prompt *(3.1)*
- [ ] **0.6** Global default rate limit; per-endpoint overrides *(1.5)*
- [ ] **0.7** Enforce quota before stream opens; debit incrementally *(1.6)*
- [ ] **0.8** Server-side `(provider, model)` allowlist *(1.7)*
- [ ] **0.9** Upload size caps on both `/api/sources/upload` branches + body-size middleware *(1.9)*
- [ ] **0.10** Mermaid `securityLevel: 'strict'` + DOMPurify; add CSP header *(1.11)*
- [ ] **0.11** Compound index on `usage_logs(user_id, date)`; unique index on `manuscripts(user_id, topic)` *(2.6, 2.13)*
- [ ] **0.12** Remove input blocklist; delimited-content prompting + output validation *(1.8, 3.15)*
- [ ] **0.13** Complete user deletion (sources, literature, GridFS) *(1.14)*
- [ ] **0.14** Stop logging full PDF structures; add log rotation *(1.15)*
- [ ] **0.15** Configure trusted-proxy handling for rate limiting *(1.12)*
- [ ] **0.16** Drop `allow_credentials`; validate/trim CORS origins *(1.13)*

---

## Phase 1 — Make it durable

- [ ] **1.1** Project / Workspace entity — persisted corpus, evidence, screening, versioned manuscript + provenance *(3.2, 3.13)*
- [ ] **1.2** Move in-process caches to MongoDB / Redis, keyed by content hash, scoped by tenancy *(2.3)*
- [ ] **1.3** Persist embeddings permanently, keyed by DOI / normalized title *(2.3)*
- [ ] **1.4** Shared `httpx.AsyncClient` with keepalive on lifespan startup *(2.4)*
- [ ] **1.5** Background job + granular progress stream for the research pipeline *(2.1)*
- [ ] **1.6** Embedding similarity for bulk relevance; LLM only for borderline band *(2.2)*
- [ ] **1.7** Real provider token accounting (`include_usage`, `usage_metadata`) *(1.6)*
- [ ] **1.8** SSE disconnect detection and upstream cancellation *(2.10)*
- [ ] **1.9** Fix HuggingFace thread-pool leak, fallback semaphore, loop-bound semaphores *(2.8, 2.9)*
- [ ] **1.10** Manuscript version history with restore *(2.13)*
- [ ] **1.11** Cursor pagination on all list endpoints *(2.14)*
- [ ] **1.12** Fix admin N+1; single aggregation *(2.7)*
- [ ] **1.13** Frontend: single API client, route-level code splitting, React Query, drop `sessionStorage` paper cache *(2.12)*
- [ ] **1.14** Refresh-token rotation; cache user/revocation lookups *(1.17, 2.5)*
- [ ] **1.15** Migrate `python-jose` → PyJWT *(1.17)*
- [ ] **1.16** Consent surface + DPA for LlamaCloud / GROBID; consider self-hosting GROBID *(1.16)*

---

## Phase 2 — Make the output real

- [ ] **2.1** Full-text ingestion via Unpaywall / Europe PMC / CORE; chunk + passage embeddings with offsets *(3.4)*
- [ ] **2.2** Citation snowballing — backward/forward via OpenAlex + Semantic Scholar *(3.3)*
- [ ] **2.3** Query expansion, Boolean strategy, controlled-vocabulary mapping *(3.3)*
- [ ] **2.4** Passage-anchored generation — claim-by-claim, entailment check, sentence regen on failure *(3.5)*
- [ ] **2.5** Replace regex numerical validator with provenance-linked number extraction *(3.5)*
- [ ] **2.6** PRISMA-style audit trail (DBs, dates, queries, N retrieved / screened / excluded) *(3.2)*
- [ ] **2.7** Provenance-only chart generation from extracted numbers with citations *(3.1)*
- [ ] **2.8** Real BibTeX types/fields; CSL; Crossref DOI validation; RIS/Zotero import-export *(3.10)*
- [ ] **2.9** Sandboxed LaTeX compile (Tectonic) + camera-ready PDF preview *(3.11)*
- [ ] **2.10** Figures and tables as first-class entities with captions and `\ref` *(3.12)*

---

## Phase 3 — Research platform

- [ ] **3.1** Experimental data layer — dataset upload, stats engine, real figures, data-availability statements *(3.6)*
- [ ] **3.2** Novelty / prior-art check against corpus + OpenAlex *(3.7)*
- [ ] **3.3** Similarity / plagiarism check against retrieved corpus *(3.8)*
- [ ] **3.4** Adversarial reviewer loop against venue-specific criteria *(3.9)*
- [ ] **3.5** Coauthor collaboration — shared projects, comments, track changes, roles *(3.13)*
- [ ] **3.6** Compliance & disclosure — AI disclosure, COI, ethics, ORCID, funding, license *(3.14)*
- [ ] **3.7** Saved-search alerting with digest emails via Brevo *(3.16)*
- [ ] **3.8** Submission-portal integration / export bundles per publisher

---

## Other 1.17 items (not in numbered phases)

- [ ] Password complexity beyond `min_length=8`
- [ ] JWT `aud` / `iss` verification
- [ ] Check Google `email_verified` claim
- [ ] Guard `grid_out.metadata` None → 500
- [ ] `TrustedHostMiddleware` / HTTPS redirect
- [ ] Stop keeping `client_secret_*.json` in `backend/integrations/`
- [ ] Stop returning raw `str(e)` from admin status checks
- [ ] Pin SSRF to resolved IP + port allowlist 80/443 *(1.10)*
- [ ] Cascade continuation quadratic cost *(2.11)*
