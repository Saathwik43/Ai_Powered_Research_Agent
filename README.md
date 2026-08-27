# Research Agent

An AI-assisted platform for academic research and publishing. It supports the workflow end to end: discovering a topic, running a literature survey across nine bibliographic databases, reading and questioning source papers, drafting a manuscript grounded in that literature, and selecting a publication venue.

- **Live deployment:** [ai-powered-research-agent-live.onrender.com](https://ai-powered-research-agent-live.onrender.com)
- **Architecture:** [`architecture_and_workflow.md`](architecture_and_workflow.md) — component diagram, end-to-end request sequence, search pipeline, and evidence ladder.

## Capabilities

- **Topic discovery.** Suggests research directions for an area of interest. Keywords are derived by TF-IDF over the retrieved corpus rather than by an LLM, which keeps the suggestions cheap and reproducible.
- **Literature survey.** A single query fans out in parallel to nine databases — Semantic Scholar, OpenAlex, Crossref, PubMed, arXiv, Springer Nature, Europe PMC, DOAJ, and a set of checked-out GitHub knowledge repositories. Results are deduplicated by DOI, arXiv identifier, or normalized title, then ranked and relevance-filtered. Unpaywall enriches open-access links after deduplication.
- **Document reader.** Upload a paper or supply an open-access URL and query it directly. Remote fetches pass through an SSRF guard; parsing runs in-process.
- **Manuscript drafting.** Sections are generated with live streaming. A mid-stream provider failure continues on the next provider rather than restarting. Revisions can target a whole section, a selected sentence range, or a single diagram, and are applied through an accept/reject diff.
- **Grounding checks.** Generated text is checked against the retrieved sources, numerical claims are validated, and every `[N]` citation is resolved against the reference list. Unsupported claims are flagged rather than shipped silently.
- **Gap analysis.** Identifies what the retrieved literature does not cover, based on extracted evidence rather than abstracts alone.
- **Venue recommendation.** Matches a manuscript against journals and conferences and produces a formatting checklist aligned to the venue's guidelines.
- **LaTeX export.** Produces an Overleaf-ready archive with the venue template and matched references. Sandboxed compilation to a camera-ready PDF is not yet implemented.
- **Version history.** Each save snapshots the state it replaces, so any save can be rolled back; the restore is itself reversible.
- **Administration.** Per-user roles, quotas, and suspension, alongside live source health and API telemetry.

## Architecture overview

**Provider cascade.** `ai/llm_provider.py` fails over across providers in the order OpenAI, Gemini, Groq, Cerebras, Mistral, HuggingFace. A provider joins the chain only when its key is configured, so the effective order on a given deployment is that list minus whatever is unset. OpenRouter and NVIDIA are reachable only by setting `MANUSCRIPT_PROVIDER` explicitly and are never part of the automatic chain. Gemini contexts above roughly 32k tokens use prompt caching.

**Evidence ladder.** Full text is resolved through five tiers, cheapest first: arXiv LaTeXML HTML, arXiv LaTeX e-print, Europe PMC JATS, in-process PyMuPDF structure parsing, and finally an LLM over title and abstract. No hosted document-parsing service is involved, and nothing is cloned at request time.

**Request pipeline.** Every request crosses four middleware layers before reaching a route — CORS, a body-size cap, the rate limiter, then security headers — ordered so that a rejection still carries CORS headers and reaches the browser as a stated reason rather than an unexplained network failure.

## Technology stack

| Layer | Components |
| --- | --- |
| Frontend | React 19, Vite, React Router, Tailwind CSS v4, `react-pdf` / `pdfjs-dist`, Mermaid, KaTeX, Recharts, Motion |
| Backend | FastAPI on Python 3.11/3.12, MongoDB via Motor, `httpx` with a shared connection pool |
| Models | Gemini, OpenAI, Groq, Cerebras, Mistral, HuggingFace (automatic cascade); Gemini embeddings back the semantic cache |
| Email | Brevo, for address verification and password reset |

## Getting started

### Prerequisites

- Python 3.11 or 3.12
- Node.js 20
- A reachable MongoDB instance

### Backend

```bash
cd backend
pip install -r requirements.txt
```

Create a `.env` file in `backend/`. At minimum, set `JWT_SECRET_KEY`, a reachable `MONGO_URI`, and one model provider key; see [Configuration](#configuration).

Optionally check out the GitHub knowledge repositories, which are read from disk and never cloned during a request:

```bash
python -m scripts.sync_github_repos
```

Without this step the `/api/github/*` routes return empty results and GitHub contributes nothing to search. Everything else is unaffected.

Start the server:

```bash
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
```

Create a `.env` file with `VITE_API_URL=http://localhost:8000`, and `VITE_GOOGLE_CLIENT_ID` if Google sign-in is required. Then:

```bash
npm run dev
```

## Configuration

### Required

| Variable | Notes |
| --- | --- |
| `JWT_SECRET_KEY` | The application refuses to start without it |
| `MONGO_URI` | Defaults to `mongodb://localhost:27017` |
| One provider key | `GEMINI_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`, or `HUGGINGFACEHUB_API_TOKEN` |

### Authentication and email

`BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, and `BREVO_SENDER_NAME` configure outbound mail. `FRONTEND_URL` (default `http://localhost:5173`) is the base of the verification and reset links. Accounts are created with `email_verified=false` and receive no session token until the emailed link is followed, so email signup cannot complete without Brevo configured. `GOOGLE_CLIENT_ID` enables Google sign-in.

### Model providers

`MANUSCRIPT_PROVIDER` selects the strategy and defaults to `auto`. Set it to `openrouter` or `nvidia` to use those providers, together with `OPENROUTER_API_KEY` or `NVIDIA_API_KEY`; OpenRouter additionally sends `APP_PUBLIC_URL` as its referer. Model overrides are available per provider: `GEMINI_MODEL`, `OPENAI_MODEL`, `GROQ_MODEL`, `CEREBRAS_MODEL`, `MISTRAL_MODEL`, `NVIDIA_MODEL`, `OPENROUTER_MODEL`, and `HUGGINGFACE_MANUSCRIPT_MODEL`. HuggingFace reads `HUGGINGFACEHUB_API_TOKEN`, falling back to `HF_TOKEN`.

### Search sources

All source keys are optional; a source without its key is skipped rather than failing the search. `SPRINGER_META_API_KEY` is required for Springer to return anything. `SEMANTIC_SCHOLAR_API_KEY` and `PUBMED_API_KEY` raise the applicable rate limits. `CROSSREF_MAILTO` and `OPENALEX_API_KEY` affect politeness and priority only — OpenAlex works without a key.

### Sessions and caching

| Variable | Default | Purpose |
| --- | --- | --- |
| `ACCESS_TOKEN_EXPIRE_HOURS` | `1` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `14` | How long a signed-in tab keeps working |
| `JWT_REFRESH_SECRET_KEY` | derived from `JWT_SECRET_KEY` | Set only for independent rotation |
| `JWT_VERIFY_SECRET_KEY`, `JWT_RESET_SECRET_KEY` | derived | Verification and reset links are signed with their own keys |
| `SHARED_CACHE_ENABLED` | on | Set to `0` to keep every cache in-process |
| `SHARED_CACHE_TIMEOUT` | `2.5` | A cache read must never outlast the work it skips |

### Request limits

`MAX_UPLOAD_BYTES` (10 MB), `MAX_REQUEST_BODY_BYTES` (16 MB), `MAX_URL_FETCH_BYTES` (5 MB), `DEFAULT_RATE_LIMIT`, and `TRUSTED_PROXY_HOPS`.

### Optional

`LLAMA_CLOUD_API_KEY`. Without it the third-party processing consent prompt is hidden and PDFs are always parsed locally.

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest -v --strict-markers
```

Tests marked `integration` make real API calls and are skipped unless `--run-integration` is passed. No test requires a real credential: each mocks its provider or sets the environment it needs. CI runs the backend suite on Python 3.11 and 3.12, and lints and builds the frontend.

## Deployment

- Frontend API calls are routed via `VITE_API_URL`; backend CORS is managed via `CORS_ORIGINS`.
- Set `TRUSTED_PROXY_HOPS` to the number of proxies in front of the application (`1` on Render). It defaults to `0`, which ignores `X-Forwarded-For` — the correct default for a directly exposed application, but behind a platform proxy it causes every anonymous caller to share a single rate-limit bucket.
- Append `python -m scripts.sync_github_repos` to the build command, or the GitHub knowledge source remains empty. Nothing is cloned at runtime.

## Repository layout

```text
backend/
  ai/            model providers, generation, grounding, evidence extraction
  core/          auth, config, caching, retries, SSRF guard
  integrations/  the nine search sources and the pooled HTTP client
  routers/       FastAPI route definitions
  services/      source health, telemetry, background research jobs, quotas
  scripts/       repository sync, API doctor, semantic-cache evaluation
  tests/
frontend/
  src/pages/     one module per workspace surface
  src/lib/       single API client and query cache
architecture_and_workflow.md
```
