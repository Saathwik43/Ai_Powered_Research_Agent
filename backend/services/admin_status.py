"""Live health checks for all third-party integrations.

Probes exercise the *same operation the app uses* (tiny generate / tiny search),
not catalogue or identity endpoints. Results answer "can I use this next?"

Live in-flight / last-call stats come from api_telemetry and answer
"is it in use right now?" and "what happened last time?".
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

from ai.model_allowlist import resolve_groq_model
from services.api_telemetry import inflight_total, live_for
from integrations.github_knowledge import REPOS

Source = dict[str, Any]
CheckFn = Callable[[], Awaitable[Source]]

CATEGORY_ORDER = [
    "LLM Providers",
    "Literature Sources",
    "Document Processing",
    "Infrastructure",
]


def _result(
    name: str,
    category: str,
    status: str,
    details: str,
    latency_ms: Optional[int] = None,
    requires_key: Optional[str] = None,
    probe: Optional[dict] = None,
) -> Source:
    out: Source = {
        "name": name,
        "category": category,
        "type": category,
        "status": status,
        "details": details,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "phase": "before",
    }
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    if requires_key:
        out["requires_key"] = requires_key
    if probe:
        out["probe"] = probe
    return out


def _no_key(name: str, category: str, key: str) -> Source:
    return _result(name, category, "no_key", f"{key} missing", requires_key=key)


async def _probe_chat(
    name: str,
    *,
    url: str,
    key: str,
    key_name: str,
    model: str,
    extra_headers: Optional[dict] = None,
    timeout: float = 12.0,
) -> Source:
    if not key:
        return _no_key(name, "LLM Providers", key_name)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word ok"}],
        "max_tokens": 4,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(url, headers=headers, json=payload)
        latency = round((time.time() - start) * 1000)
        probe = {"operation": "generate", "model": model, "http_status": res.status_code}
        if res.status_code == 200:
            return _result(name, "LLM Providers", "operational", f"Generation OK ({model})", latency, key_name, probe)
        if res.status_code == 429:
            return _result(name, "LLM Providers", "rate_limited", "Rate limited on generate", latency, key_name, probe)
        if res.status_code == 402:
            return _result(name, "LLM Providers", "offline", "Payment / credits required", latency, key_name, probe)
        if res.status_code in (401, 403):
            return _result(name, "LLM Providers", "offline", f"Unauthorized (HTTP {res.status_code})", latency, key_name, probe)
        return _result(name, "LLM Providers", "offline", f"Generate HTTP {res.status_code}", latency, key_name, probe)
    except httpx.TimeoutException:
        return _result(
            name, "LLM Providers", "degraded",
            f"Generate timed out (>{int(timeout)}s) — too slow for interactive use",
            requires_key=key_name,
            probe={"operation": "generate", "model": model},
        )
    except Exception:
        return _result(name, "LLM Providers", "offline", "Generate unreachable", requires_key=key_name)


async def check_groq() -> Source:
    return await _probe_chat(
        "Groq",
        url="https://api.groq.com/openai/v1/chat/completions",
        key=os.getenv("GROQ_API_KEY", ""),
        key_name="GROQ_API_KEY",
        model=resolve_groq_model(),
    )


async def check_openai() -> Source:
    return await _probe_chat(
        "OpenAI",
        url="https://api.openai.com/v1/chat/completions",
        key=os.getenv("OPENAI_API_KEY", ""),
        key_name="OPENAI_API_KEY",
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    )


async def check_mistral() -> Source:
    return await _probe_chat(
        "Mistral",
        url="https://api.mistral.ai/v1/chat/completions",
        key=os.getenv("MISTRAL_API_KEY", ""),
        key_name="MISTRAL_API_KEY",
        model=os.getenv("MISTRAL_MODEL", "mistral-large-latest"),
    )


async def check_openrouter() -> Source:
    return await _probe_chat(
        "OpenRouter",
        url="https://openrouter.ai/api/v1/chat/completions",
        key=os.getenv("OPENROUTER_API_KEY", ""),
        key_name="OPENROUTER_API_KEY",
        model=os.getenv("OPENROUTER_MODEL", "~anthropic/claude-sonnet-latest"),
        extra_headers={
            "HTTP-Referer": os.getenv("APP_PUBLIC_URL", "http://localhost:5173"),
            "X-Title": "Research Agent",
        },
    )


async def check_nvidia() -> Source:
    return await _probe_chat(
        "NVIDIA NIM",
        url="https://integrate.api.nvidia.com/v1/chat/completions",
        key=os.getenv("NVIDIA_API_KEY", ""),
        key_name="NVIDIA_API_KEY",
        model=os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
        timeout=15.0,
    )


async def check_cerebras() -> Source:
    return await _probe_chat(
        "Cerebras",
        url="https://api.cerebras.ai/v1/chat/completions",
        key=os.getenv("CEREBRAS_API_KEY", ""),
        key_name="CEREBRAS_API_KEY",
        model=os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
    )


async def check_gemini() -> Source:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return _no_key("Google Gemini", "LLM Providers", "GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    start = time.time()
    try:
        from google import genai
        from google.genai import types as gt

        client = genai.Client(api_key=key)
        resp = await client.aio.models.generate_content(
            model=model,
            contents="Reply with the single word ok",
            config=gt.GenerateContentConfig(
                max_output_tokens=64,
                temperature=0,
                thinking_config=gt.ThinkingConfig(thinking_budget=128),
            ),
        )
        latency = round((time.time() - start) * 1000)
        text = (resp.text or "").strip()
        probe = {"operation": "generate", "model": model}
        if text:
            return _result("Google Gemini", "LLM Providers", "operational", f"Generation OK ({model})", latency, "GEMINI_API_KEY", probe)
        # Thinking can consume a tiny max_output budget; HTTP success still means the key/path works.
        return _result("Google Gemini", "LLM Providers", "operational", f"Generate reachable ({model})", latency, "GEMINI_API_KEY", probe)
    except Exception as e:
        latency = round((time.time() - start) * 1000)
        err = str(e)
        if "429" in err:
            return _result("Google Gemini", "LLM Providers", "rate_limited", "Rate limited on generate", latency, "GEMINI_API_KEY")
        if "400" in err:
            return _result("Google Gemini", "LLM Providers", "offline", "Invalid generate config", latency, "GEMINI_API_KEY")
        return _result("Google Gemini", "LLM Providers", "offline", type(e).__name__, latency, "GEMINI_API_KEY")


async def check_huggingface() -> Source:
    key = os.getenv("HUGGINGFACEHUB_API_TOKEN") or os.getenv("HF_TOKEN")
    model = os.getenv("HUGGINGFACE_MANUSCRIPT_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1")
    if not key:
        return _no_key("Hugging Face Inference", "LLM Providers", "HUGGINGFACEHUB_API_TOKEN")
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await client.post(
                f"https://router.huggingface.co/hf-inference/models/{model}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 4},
            )
        latency = round((time.time() - start) * 1000)
        probe = {"operation": "generate", "model": model, "http_status": res.status_code}
        if res.status_code == 200:
            return _result("Hugging Face Inference", "LLM Providers", "operational", f"Generation OK ({model})", latency, "HUGGINGFACEHUB_API_TOKEN", probe)
        return _result(
            "Hugging Face Inference", "LLM Providers", "offline",
            f"Generate HTTP {res.status_code} — model not served on hf-inference",
            latency, "HUGGINGFACEHUB_API_TOKEN", probe,
        )
    except Exception:
        return _result("Hugging Face Inference", "LLM Providers", "offline", "Generate unreachable", requires_key="HUGGINGFACEHUB_API_TOKEN")


async def _probe_search(
    name: str,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    count_fn=None,
    timeout: float = 10.0,
    requires_key: Optional[str] = None,
    empty_hint: str = "Reachable but 0 results",
) -> Source:
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers or {}) as client:
            res = await client.get(url, params=params)
        latency = round((time.time() - start) * 1000)
        count = 0
        body_error = None
        data = None
        content_type = (res.headers.get("content-type") or "").lower()
        if "json" in content_type:
            try:
                data = res.json()
                if isinstance(data, dict) and data.get("error"):
                    body_error = str(data["error"])[:160]
            except Exception:
                data = None
        try:
            if count_fn:
                count = int(count_fn(res, data) or 0)
        except Exception:
            count = 0
        probe = {"operation": "search", "http_status": res.status_code, "items": count}
        if res.status_code == 429:
            return _result(name, "Literature Sources", "rate_limited", "Rate limited on search", latency, requires_key, probe)
        if res.status_code in (401, 403):
            return _result(name, "Literature Sources", "offline", f"Unauthorized (HTTP {res.status_code})", latency, requires_key, probe)
        if res.status_code >= 500:
            return _result(name, "Literature Sources", "offline", f"Upstream HTTP {res.status_code}", latency, requires_key, probe)
        if body_error:
            return _result(name, "Literature Sources", "offline", body_error, latency, requires_key, probe)
        if res.status_code == 200 and count > 0:
            return _result(name, "Literature Sources", "operational", f"Search OK · {count} hit(s)", latency, requires_key, probe)
        if res.status_code == 200:
            return _result(name, "Literature Sources", "degraded", empty_hint, latency, requires_key, probe)
        return _result(name, "Literature Sources", "degraded", f"HTTP {res.status_code}", latency, requires_key, probe)
    except httpx.TimeoutException:
        return _result(name, "Literature Sources", "degraded", f"Search timed out (>{int(timeout)}s)", requires_key=requires_key)
    except Exception:
        return _result(name, "Literature Sources", "offline", "Search unreachable", requires_key=requires_key)


async def check_semantic_scholar() -> Source:
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    headers = {"x-api-key": key} if key else {}
    result = await _probe_search(
        "Semantic Scholar",
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": "solar cells", "limit": 1, "fields": "title"},
        headers=headers,
        count_fn=lambda _r, d: len((d or {}).get("data") or []),
        empty_hint="Search 429 or empty — bulk fallback may still work at runtime",
    )
    if result["status"] == "rate_limited":
        result["details"] = (
            "Keyword search rate-limited (unauthenticated S2 is ~1 req/s). "
            "Runtime search retries then uses the bulk endpoint. Set SEMANTIC_SCHOLAR_API_KEY to raise the cap."
        )
        return result
    return result


async def check_arxiv() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "")
    ua = f"AI-Powered-Research-Agent/1.0 (contact: {email})" if email else "AI-Powered-Research-Agent/1.0"
    return await _probe_search(
        "arXiv",
        "https://export.arxiv.org/api/query",
        params={"search_query": "all:electron", "max_results": 1},
        headers={"User-Agent": ua},
        count_fn=lambda r, _d: r.text.count("<entry>"),
    )


async def check_pubmed() -> Source:
    params: dict[str, Any] = {"db": "pubmed", "term": "cancer", "retmode": "json", "retmax": 1}
    key = os.getenv("PUBMED_API_KEY", "")
    if key:
        params["api_key"] = key
    return await _probe_search(
        "PubMed / NCBI",
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=params,
        count_fn=lambda _r, d: len(((d or {}).get("esearchresult") or {}).get("idlist") or []),
        requires_key="PUBMED_API_KEY",
        timeout=8.0,
    )


async def check_openalex() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "")
    params: dict[str, Any] = {
        "search": "machine learning",
        "per-page": 1,
        "select": "id,title",
    }
    if email:
        params["mailto"] = email
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.getenv("OPENALEX_API_KEY")
    return await _probe_search(
        "OpenAlex",
        "https://api.openalex.org/works",
        params=params,
        headers={"User-Agent": f"ResearchAgent/1.0 (mailto:{email})" if email else "ResearchAgent/1.0"},
        count_fn=lambda _r, d: len((d or {}).get("results") or []),
    )


async def check_crossref() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "research-agent@example.com")
    return await _probe_search(
        "Crossref",
        "https://api.crossref.org/works",
        params={"query": "machine learning", "rows": 1},
        headers={"User-Agent": f"ResearchAgent/1.0 (mailto:{email})"},
        count_fn=lambda _r, d: len((((d or {}).get("message") or {}).get("items")) or []),
        timeout=12.0,
    )


async def check_springer() -> Source:
    key = os.getenv("SPRINGER_META_API_KEY")
    if not key:
        return _no_key("Springer Nature", "Literature Sources", "SPRINGER_META_API_KEY")
    return await _probe_search(
        "Springer Nature",
        "https://api.springernature.com/meta/v2/json",
        params={"q": "machine learning", "api_key": key, "p": 1},
        count_fn=lambda _r, d: len((d or {}).get("records") or []),
        requires_key="SPRINGER_META_API_KEY",
    )


async def check_europepmc() -> Source:
    return await _probe_search(
        "Europe PMC",
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": "machine learning", "format": "json", "pageSize": 1, "resultType": "core"},
        count_fn=lambda _r, d: len((((d or {}).get("resultList") or {}).get("result")) or []),
    )


async def check_doaj() -> Source:
    return await _probe_search(
        "DOAJ",
        "https://doaj.org/api/search/articles/machine%20learning",
        params={"pageSize": 1},
        count_fn=lambda _r, d: len((d or {}).get("results") or []),
    )


async def check_unpaywall() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "")
    if not email:
        return _no_key("Unpaywall", "Literature Sources", "CROSSREF_MAILTO")
    return await _probe_search(
        "Unpaywall",
        "https://api.unpaywall.org/v2/10.1038/nature12373",
        params={"email": email},
        count_fn=lambda _r, d: 1 if (d or {}).get("best_oa_location") else 0,
        requires_key="CROSSREF_MAILTO",
    )


async def check_github_knowledge() -> Source:
    present = [name for name, meta in REPOS.items() if meta["dir"].exists()]
    if present:
        return _result(
            "GitHub Knowledge Repos",
            "Literature Sources",
            "operational",
            f"{len(present)}/{len(REPOS)} local repos synced ({', '.join(present)})",
            probe={"operation": "local_index", "items": len(present)},
        )
    return _result(
        "GitHub Knowledge Repos",
        "Literature Sources",
        "degraded",
        "No local knowledge repos cloned yet (lazy-synced on first search)",
    )


async def check_pdf_structure() -> Source:
    """PDF structure parsing runs in-process (PyMuPDF), so this reports whether
    the library imports rather than whether a remote service answers. It
    replaces the old GROBID probe: every free hosted GROBID instance is gone
    and the service is too heavy for the deploy target, so there is no
    third-party dependency in this path any more."""
    try:
        start = time.time()
        import fitz  # noqa: F401  - import *is* the health check

        latency = round((time.time() - start) * 1000)
        return _result(
            "PDF Structure",
            "Document Processing",
            "operational",
            "In-process (PyMuPDF), no external dependency",
            latency,
        )
    except Exception as e:
        return _result("PDF Structure", "Document Processing", "offline", f"PyMuPDF unavailable: {e}")


async def check_llamacloud() -> Source:
    key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not key:
        return _no_key("LlamaCloud", "Document Processing", "LLAMA_CLOUD_API_KEY")
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.cloud.llamaindex.ai/api/v1/parsing/supported_file_extensions",
                headers={"Authorization": f"Bearer {key}"},
            )
        latency = round((time.time() - start) * 1000)
        if res.status_code == 200:
            return _result("LlamaCloud", "Document Processing", "operational", "Parse API reachable", latency, "LLAMA_CLOUD_API_KEY")
        if res.status_code in (401, 403):
            return _result("LlamaCloud", "Document Processing", "offline", f"Unauthorized (HTTP {res.status_code})", latency, "LLAMA_CLOUD_API_KEY")
        return _result("LlamaCloud", "Document Processing", "degraded", f"HTTP {res.status_code}", latency, "LLAMA_CLOUD_API_KEY")
    except Exception:
        return _result("LlamaCloud", "Document Processing", "offline", "Unreachable", requires_key="LLAMA_CLOUD_API_KEY")


async def check_mongodb() -> Source:
    try:
        from core.database import client

        start = time.time()
        await client.admin.command("ping")
        latency = round((time.time() - start) * 1000)
        return _result("MongoDB", "Infrastructure", "operational", "Ping OK", latency)
    except Exception:
        return _result("MongoDB", "Infrastructure", "offline", "Ping failed")


async def check_brevo() -> Source:
    key = os.getenv("BREVO_API_KEY")
    if not key:
        return _no_key("Brevo Email", "Infrastructure", "BREVO_API_KEY")
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.brevo.com/v3/account",
                headers={"api-key": key, "accept": "application/json"},
            )
        latency = round((time.time() - start) * 1000)
        sender = os.getenv("BREVO_SENDER_EMAIL")
        if res.status_code == 200 and not sender:
            return _result("Brevo Email", "Infrastructure", "degraded", "Account OK but BREVO_SENDER_EMAIL missing", latency, "BREVO_API_KEY")
        if res.status_code == 200:
            return _result("Brevo Email", "Infrastructure", "operational", "Account OK", latency, "BREVO_API_KEY")
        return _result("Brevo Email", "Infrastructure", "offline", f"HTTP {res.status_code}", latency, "BREVO_API_KEY")
    except Exception:
        return _result("Brevo Email", "Infrastructure", "offline", "Unreachable", requires_key="BREVO_API_KEY")


async def check_google_oauth() -> Source:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        return _no_key("Google OAuth", "Infrastructure", "GOOGLE_CLIENT_ID")
    return _result(
        "Google OAuth",
        "Infrastructure",
        "operational",
        "Client ID configured (sign-in ready)",
        requires_key="GOOGLE_CLIENT_ID",
    )


CHECKS: list[CheckFn] = [
    check_groq,
    check_gemini,
    check_openai,
    check_mistral,
    check_openrouter,
    check_nvidia,
    check_cerebras,
    check_huggingface,
    check_semantic_scholar,
    check_arxiv,
    check_pubmed,
    check_openalex,
    check_crossref,
    check_springer,
    check_europepmc,
    check_doaj,
    check_unpaywall,
    check_github_knowledge,
    check_pdf_structure,
    check_llamacloud,
    check_mongodb,
    check_brevo,
    check_google_oauth,
]

CHECK_BY_NAME: dict[str, CheckFn] = {
    "Groq": check_groq,
    "Google Gemini": check_gemini,
    "OpenAI": check_openai,
    "Mistral": check_mistral,
    "OpenRouter": check_openrouter,
    "NVIDIA NIM": check_nvidia,
    "Cerebras": check_cerebras,
    "Hugging Face Inference": check_huggingface,
    "Semantic Scholar": check_semantic_scholar,
    "arXiv": check_arxiv,
    "PubMed / NCBI": check_pubmed,
    "OpenAlex": check_openalex,
    "Crossref": check_crossref,
    "Springer Nature": check_springer,
    "Europe PMC": check_europepmc,
    "DOAJ": check_doaj,
    "Unpaywall": check_unpaywall,
    "GitHub Knowledge Repos": check_github_knowledge,
    "PDF Structure": check_pdf_structure,
    "LlamaCloud": check_llamacloud,
    "MongoDB": check_mongodb,
    "Brevo Email": check_brevo,
    "Google OAuth": check_google_oauth,
}

# Admin display name -> search_all() task name (or "Unpaywall" for OA enrich).
SEARCH_SKIP_MAP = {
    "Semantic Scholar": "SemanticScholar",
    "OpenAlex": "OpenAlex",
    "Crossref": "Crossref",
    "PubMed / NCBI": "PubMed",
    "arXiv": "arXiv",
    "Springer Nature": "Springer",
    "Europe PMC": "EuropePMC",
    "DOAJ": "DOAJ",
    "GitHub Knowledge Repos": "GitHub",
    "Unpaywall": "Unpaywall",
}

_DISABLED: set[str] = set()
_DISABLED_LOADED = False
_DISABLED_LOCK = asyncio.Lock()
_SETTINGS_ID = "source_toggles"


async def _ensure_disabled_loaded() -> None:
    global _DISABLED_LOADED
    if _DISABLED_LOADED:
        return
    async with _DISABLED_LOCK:
        if _DISABLED_LOADED:
            return
        try:
            from core.database import db
            doc = await db["admin_settings"].find_one({"_id": _SETTINGS_ID})
            names = (doc or {}).get("disabled") or []
            _DISABLED.clear()
            for n in names:
                if isinstance(n, str) and n:
                    _DISABLED.add(n)
        except Exception:
            pass
        _DISABLED_LOADED = True


def is_enabled(name: str) -> bool:
    return name not in _DISABLED


def get_disabled_display_names() -> set[str]:
    return set(_DISABLED)


def get_disabled_search_tasks() -> set[str]:
    out = set()
    for display in _DISABLED:
        task = SEARCH_SKIP_MAP.get(display)
        if task:
            out.add(task)
    return out


async def set_source_enabled(name: str, enabled: bool) -> dict[str, Any]:
    if name not in SEARCH_SKIP_MAP:
        raise KeyError(name)
    await _ensure_disabled_loaded()
    if enabled:
        _DISABLED.discard(name)
    else:
        _DISABLED.add(name)
    try:
        from core.database import db
        await db["admin_settings"].update_one(
            {"_id": _SETTINGS_ID},
            {"$set": {"disabled": sorted(_DISABLED), "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception:
        pass
    return {"name": name, "enabled": enabled, "disabled": sorted(_DISABLED)}


async def probe_one(name: str) -> Source:
    fn = CHECK_BY_NAME.get(name)
    if not fn:
        raise KeyError(name)
    result = await fn()
    cached = _STATUS_CACHE.get("payload")
    if cached and isinstance(cached.get("sources"), list):
        patched = False
        clean = {k: v for k, v in result.items() if k != "live"}
        for i, src in enumerate(cached["sources"]):
            if src.get("name") == name:
                cached["sources"][i] = clean
                patched = True
                break
        if not patched:
            cached["sources"].append(clean)
    return _merge_live(result)

_STATUS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_STATUS_TTL_SEC = 45.0
_STATUS_LOCK = asyncio.Lock()


def _merge_live(src: Source) -> Source:
    live = live_for(src.get("name") or "")
    merged = dict(src)
    name = src.get("name") or ""
    merged["live"] = live
    merged["enabled"] = is_enabled(name)
    merged["skippable"] = name in SEARCH_SKIP_MAP
    in_flight = int(live.get("in_flight") or 0)
    if in_flight > 0:
        merged["in_use"] = True
    return merged


def _summarize(sources: list[Source]) -> dict[str, int]:
    return {
        "total": len(sources),
        "operational": sum(1 for s in sources if s.get("status") == "operational"),
        "degraded": sum(1 for s in sources if s.get("status") in ("degraded", "rate_limited")),
        "offline": sum(1 for s in sources if s.get("status") in ("offline", "no_key")),
        "in_flight": inflight_total(),
    }


def _attach_categories(sources: list[Source]) -> list[dict[str, Any]]:
    by_category: dict[str, list[Source]] = {cat: [] for cat in CATEGORY_ORDER}
    for src in sources:
        cat = src.get("category") or "Infrastructure"
        by_category.setdefault(cat, []).append(src)
    return [
        {
            "name": cat,
            "sources": by_category.get(cat, []),
            "operational": sum(1 for s in by_category.get(cat, []) if s.get("status") == "operational"),
            "total": len(by_category.get(cat, [])),
        }
        for cat in CATEGORY_ORDER
        if by_category.get(cat)
    ]


async def collect_system_status(*, force: bool = False) -> dict[str, Any]:
    await _ensure_disabled_loaded()
    now = time.time()
    cached = _STATUS_CACHE.get("payload")
    if not force and cached and (now - float(_STATUS_CACHE.get("ts") or 0)) < _STATUS_TTL_SEC:
        sources = [_merge_live(s) for s in cached.get("sources") or []]
        categories = _attach_categories(sources)
        return {
            "sources": [s for cat in categories for s in cat["sources"]],
            "categories": categories,
            "summary": _summarize(sources),
            "cached": True,
            "cache_ttl_sec": _STATUS_TTL_SEC,
        }

    async with _STATUS_LOCK:
        now = time.time()
        cached = _STATUS_CACHE.get("payload")
        if not force and cached and (now - float(_STATUS_CACHE.get("ts") or 0)) < _STATUS_TTL_SEC:
            sources = [_merge_live(s) for s in cached.get("sources") or []]
            categories = _attach_categories(sources)
            return {
                "sources": [s for cat in categories for s in cat["sources"]],
                "categories": categories,
                "summary": _summarize(sources),
                "cached": True,
                "cache_ttl_sec": _STATUS_TTL_SEC,
            }

        results = await asyncio.gather(*(check() for check in CHECKS), return_exceptions=True)
        sources: list[Source] = []
        for check, result in zip(CHECKS, results):
            if isinstance(result, Exception):
                sources.append(
                    _result(
                        check.__name__.replace("check_", "").replace("_", " ").title(),
                        "Infrastructure",
                        "offline",
                        type(result).__name__,
                    )
                )
            else:
                sources.append(result)

        probe_payload = {
            "sources": [{k: v for k, v in s.items() if k != "live"} for s in sources],
        }
        _STATUS_CACHE["ts"] = time.time()
        _STATUS_CACHE["payload"] = probe_payload

        live_sources = [_merge_live(s) for s in sources]
        categories = _attach_categories(live_sources)
        return {
            "sources": [s for cat in categories for s in cat["sources"]],
            "categories": categories,
            "summary": _summarize(live_sources),
            "cached": False,
            "cache_ttl_sec": _STATUS_TTL_SEC,
        }
