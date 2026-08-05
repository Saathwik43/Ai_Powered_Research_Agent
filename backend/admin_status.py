"""Live health checks for all third-party integrations used by the app."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Optional

import httpx

from ai.grobid_client import _GROBID_BASE_URL
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
) -> Source:
    out: Source = {
        "name": name,
        "category": category,
        "type": category,
        "status": status,
        "details": details,
    }
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    if requires_key:
        out["requires_key"] = requires_key
    return out


def _classify_http(status_code: int) -> tuple[str, str]:
    if status_code in (200, 201, 202, 204):
        return "operational", "Reachable"
    if status_code == 429:
        return "rate_limited", "Rate limit active"
    if status_code in (401, 403):
        return "offline", f"Unauthorized (HTTP {status_code})"
    if status_code == 402:
        return "offline", "Payment / billing required (HTTP 402)"
    if status_code in (301, 302, 307, 308):
        return "degraded", f"Unexpected redirect (HTTP {status_code})"
    if status_code >= 500:
        return "offline", f"Upstream error (HTTP {status_code})"
    return "degraded", f"HTTP {status_code}"


async def _timed_get(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: float = 4.0,
    follow_redirects: bool = True,
    accept: Optional[set[int]] = None,
) -> tuple[int, int, Optional[str]]:
    start = time.time()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
        res = await client.get(url, headers=headers or {}, params=params)
    latency = round((time.time() - start) * 1000)
    if accept and res.status_code in accept:
        return res.status_code, latency, None
    return res.status_code, latency, None


async def check_groq() -> Source:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return _result("Groq", "LLM Providers", "no_key", "GROQ_API_KEY missing", requires_key="GROQ_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        status, details = _classify_http(code)
        return _result("Groq", "LLM Providers", status, details, latency, "GROQ_API_KEY")
    except Exception as e:
        return _result("Groq", "LLM Providers", "offline", str(e), requires_key="GROQ_API_KEY")


async def check_gemini() -> Source:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return _result("Google Gemini", "LLM Providers", "no_key", "GEMINI_API_KEY missing", requires_key="GEMINI_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key},
        )
        status, details = _classify_http(code)
        return _result("Google Gemini", "LLM Providers", status, details, latency, "GEMINI_API_KEY")
    except Exception as e:
        return _result("Google Gemini", "LLM Providers", "offline", str(e), requires_key="GEMINI_API_KEY")


async def check_openai() -> Source:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return _result("OpenAI", "LLM Providers", "no_key", "OPENAI_API_KEY missing", requires_key="OPENAI_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        status, details = _classify_http(code)
        return _result("OpenAI", "LLM Providers", status, details, latency, "OPENAI_API_KEY")
    except Exception as e:
        return _result("OpenAI", "LLM Providers", "offline", str(e), requires_key="OPENAI_API_KEY")


async def check_mistral() -> Source:
    key = os.getenv("MISTRAL_API_KEY")
    if not key:
        return _result("Mistral", "LLM Providers", "no_key", "MISTRAL_API_KEY missing", requires_key="MISTRAL_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        status, details = _classify_http(code)
        return _result("Mistral", "LLM Providers", status, details, latency, "MISTRAL_API_KEY")
    except Exception as e:
        return _result("Mistral", "LLM Providers", "offline", str(e), requires_key="MISTRAL_API_KEY")


async def check_openrouter() -> Source:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return _result("OpenRouter", "LLM Providers", "no_key", "OPENROUTER_API_KEY missing", requires_key="OPENROUTER_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        status, details = _classify_http(code)
        return _result("OpenRouter", "LLM Providers", status, details, latency, "OPENROUTER_API_KEY")
    except Exception as e:
        return _result("OpenRouter", "LLM Providers", "offline", str(e), requires_key="OPENROUTER_API_KEY")


async def check_nvidia() -> Source:
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        return _result("NVIDIA NIM", "LLM Providers", "no_key", "NVIDIA_API_KEY missing", requires_key="NVIDIA_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        status, details = _classify_http(code)
        return _result("NVIDIA NIM", "LLM Providers", status, details, latency, "NVIDIA_API_KEY")
    except Exception as e:
        return _result("NVIDIA NIM", "LLM Providers", "offline", str(e), requires_key="NVIDIA_API_KEY")


async def check_huggingface() -> Source:
    key = os.getenv("HUGGINGFACEHUB_API_TOKEN") or os.getenv("HF_TOKEN")
    if not key:
        return _result(
            "Hugging Face Inference",
            "LLM Providers",
            "no_key",
            "HUGGINGFACEHUB_API_TOKEN / HF_TOKEN missing",
            requires_key="HUGGINGFACEHUB_API_TOKEN",
        )
    try:
        code, latency, _ = await _timed_get(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": f"Bearer {key}"},
        )
        status, details = _classify_http(code)
        return _result("Hugging Face Inference", "LLM Providers", status, details, latency, "HUGGINGFACEHUB_API_TOKEN")
    except Exception as e:
        return _result("Hugging Face Inference", "LLM Providers", "offline", str(e), requires_key="HUGGINGFACEHUB_API_TOKEN")


async def check_semantic_scholar() -> Source:
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    headers = {"x-api-key": key} if key else {}
    try:
        code, latency, _ = await _timed_get(
            "https://api.semanticscholar.org/graph/v1/paper/autocomplete",
            params={"query": "solar"},
            headers=headers,
            accept={200, 400},
        )
        if code in (200, 400):
            return _result("Semantic Scholar", "Literature Sources", "operational", "Search reachable", latency)
        status, details = _classify_http(code)
        return _result("Semantic Scholar", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("Semantic Scholar", "Literature Sources", "offline", str(e))


async def check_arxiv() -> Source:
    try:
        code, latency, _ = await _timed_get(
            "https://export.arxiv.org/api/query",
            params={"search_query": "all:electron", "max_results": 1},
        )
        status, details = _classify_http(code)
        return _result("arXiv", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("arXiv", "Literature Sources", "offline", str(e))


async def check_pubmed() -> Source:
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": "cancer",
        "retmode": "json",
        "retmax": 1,
    }
    key = os.getenv("PUBMED_API_KEY", "")
    if key:
        params["api_key"] = key
    try:
        code, latency, _ = await _timed_get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params,
            timeout=5.0,
        )
        status, details = _classify_http(code)
        if status == "operational" and not key:
            details = "Reachable (unauthenticated; set PUBMED_API_KEY for higher limits)"
        return _result("PubMed / NCBI", "Literature Sources", status, details, latency, "PUBMED_API_KEY")
    except Exception as e:
        return _result("PubMed / NCBI", "Literature Sources", "offline", str(e), requires_key="PUBMED_API_KEY")


async def check_openalex() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "")
    params: dict[str, Any] = {"search": "machine learning", "per-page": 1}
    if email:
        params["mailto"] = email
    try:
        code, latency, _ = await _timed_get("https://api.openalex.org/works", params=params)
        status, details = _classify_http(code)
        return _result("OpenAlex", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("OpenAlex", "Literature Sources", "offline", str(e))


async def check_crossref() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "research-agent@example.com")
    try:
        code, latency, _ = await _timed_get(
            "https://api.crossref.org/works",
            params={"query": "machine learning", "rows": 1},
            headers={"User-Agent": f"ResearchAgent/1.0 (mailto:{email})"},
        )
        status, details = _classify_http(code)
        return _result("Crossref", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("Crossref", "Literature Sources", "offline", str(e))


async def check_springer() -> Source:
    key = os.getenv("SPRINGER_META_API_KEY")
    if not key:
        return _result(
            "Springer Nature",
            "Literature Sources",
            "no_key",
            "SPRINGER_META_API_KEY missing",
            requires_key="SPRINGER_META_API_KEY",
        )
    try:
        code, latency, _ = await _timed_get(
            "https://api.springernature.com/meta/v2/json",
            params={"q": "keyword:machine learning", "api_key": key, "p": 1},
        )
        status, details = _classify_http(code)
        return _result("Springer Nature", "Literature Sources", status, details, latency, "SPRINGER_META_API_KEY")
    except Exception as e:
        return _result("Springer Nature", "Literature Sources", "offline", str(e), requires_key="SPRINGER_META_API_KEY")


async def check_base() -> Source:
    try:
        code, latency, _ = await _timed_get(
            "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            params={
                "func": "PerformSearchRequest",
                "query": "machine learning",
                "hits": 1,
                "format": "json",
            },
            timeout=8.0,
        )
        status, details = _classify_http(code)
        if status == "operational":
            details = "Reachable (no API key required)"
        return _result("BASE", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("BASE", "Literature Sources", "offline", str(e))


async def check_europepmc() -> Source:
    try:
        code, latency, _ = await _timed_get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": "machine learning",
                "format": "json",
                "pageSize": 1,
                "resultType": "core",
            },
            timeout=8.0,
        )
        status, details = _classify_http(code)
        if status == "operational":
            details = "Reachable (no API key required)"
        return _result("Europe PMC", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("Europe PMC", "Literature Sources", "offline", str(e))


async def check_doaj() -> Source:
    try:
        code, latency, _ = await _timed_get(
            "https://doaj.org/api/search/articles/machine%20learning",
            params={"pageSize": 1},
            timeout=8.0,
        )
        status, details = _classify_http(code)
        if status == "operational":
            details = "Reachable (no API key required)"
        return _result("DOAJ", "Literature Sources", status, details, latency)
    except Exception as e:
        return _result("DOAJ", "Literature Sources", "offline", str(e))


async def check_core() -> Source:
    key = os.getenv("CORE_API_KEY")
    if not key:
        return _result("CORE", "Literature Sources", "no_key", "CORE_API_KEY missing", requires_key="CORE_API_KEY")
    try:
        # Prefer trailing-slash URL (non-slash returns 301)
        code, latency, _ = await _timed_get(
            "https://api.core.ac.uk/v3/search/works/",
            params={"q": "machine learning", "limit": 1},
            headers={"Authorization": f"Bearer {key}"},
            follow_redirects=True,
        )
        status, details = _classify_http(code)
        return _result("CORE", "Literature Sources", status, details, latency, "CORE_API_KEY")
    except Exception as e:
        return _result("CORE", "Literature Sources", "offline", str(e), requires_key="CORE_API_KEY")


async def check_unpaywall() -> Source:
    email = os.getenv("CROSSREF_MAILTO", "")
    if not email:
        return _result(
            "Unpaywall",
            "Literature Sources",
            "no_key",
            "CROSSREF_MAILTO required for Unpaywall",
            requires_key="CROSSREF_MAILTO",
        )
    try:
        code, latency, _ = await _timed_get(
            "https://api.unpaywall.org/v2/10.1038/nature12373",
            params={"email": email},
        )
        status, details = _classify_http(code)
        return _result("Unpaywall", "Literature Sources", status, details, latency, "CROSSREF_MAILTO")
    except Exception as e:
        return _result("Unpaywall", "Literature Sources", "offline", str(e), requires_key="CROSSREF_MAILTO")


async def check_github_knowledge() -> Source:
    present = [name for name, meta in REPOS.items() if meta["dir"].exists()]
    if present:
        return _result(
            "GitHub Knowledge Repos",
            "Literature Sources",
            "operational",
            f"{len(present)}/{len(REPOS)} local repos synced ({', '.join(present)})",
        )
    return _result(
        "GitHub Knowledge Repos",
        "Literature Sources",
        "degraded",
        "No local knowledge repos cloned yet (lazy-synced on first search)",
    )


async def check_grobid() -> Source:
    urls = [
        f"{_GROBID_BASE_URL}/api/isalive",
        "http://localhost:8070/api/isalive",
    ]
    last_err = "Unreachable"
    for url in urls:
        try:
            code, latency, _ = await _timed_get(url, timeout=3.0, follow_redirects=True)
            if code == 200:
                where = "HF Space" if "hf.space" in url else "localhost:8070"
                return _result("GROBID", "Document Processing", "operational", f"Alive ({where})", latency)
            last_err = f"HTTP {code} from {url}"
        except Exception as e:
            last_err = str(e)
    return _result(
        "GROBID",
        "Document Processing",
        "offline",
        f"{last_err}; PDF analysis falls back to PyMuPDF",
    )


async def check_mongodb() -> Source:
    try:
        from database import client

        start = time.time()
        await client.admin.command("ping")
        latency = round((time.time() - start) * 1000)
        return _result("MongoDB", "Infrastructure", "operational", "Ping OK", latency)
    except Exception as e:
        return _result("MongoDB", "Infrastructure", "offline", str(e))


async def check_brevo() -> Source:
    key = os.getenv("BREVO_API_KEY")
    if not key:
        return _result("Brevo Email", "Infrastructure", "no_key", "BREVO_API_KEY missing", requires_key="BREVO_API_KEY")
    try:
        code, latency, _ = await _timed_get(
            "https://api.brevo.com/v3/account",
            headers={"api-key": key, "accept": "application/json"},
        )
        status, details = _classify_http(code)
        sender = os.getenv("BREVO_SENDER_EMAIL")
        if status == "operational" and not sender:
            status, details = "degraded", "Account OK but BREVO_SENDER_EMAIL missing"
        return _result("Brevo Email", "Infrastructure", status, details, latency, "BREVO_API_KEY")
    except Exception as e:
        return _result("Brevo Email", "Infrastructure", "offline", str(e), requires_key="BREVO_API_KEY")


async def check_google_oauth() -> Source:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        return _result(
            "Google OAuth",
            "Infrastructure",
            "no_key",
            "GOOGLE_CLIENT_ID missing",
            requires_key="GOOGLE_CLIENT_ID",
        )
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
    check_huggingface,
    check_semantic_scholar,
    check_arxiv,
    check_pubmed,
    check_openalex,
    check_crossref,
    check_springer,
    check_core,
    check_base,
    check_europepmc,
    check_doaj,
    check_unpaywall,
    check_github_knowledge,
    check_grobid,
    check_mongodb,
    check_brevo,
    check_google_oauth,
]

_STATUS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_STATUS_TTL_SEC = 45.0
_STATUS_LOCK = asyncio.Lock()


async def collect_system_status(*, force: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = _STATUS_CACHE.get("payload")
    if not force and cached and (now - float(_STATUS_CACHE.get("ts") or 0)) < _STATUS_TTL_SEC:
        return cached

    async with _STATUS_LOCK:
        now = time.time()
        cached = _STATUS_CACHE.get("payload")
        if not force and cached and (now - float(_STATUS_CACHE.get("ts") or 0)) < _STATUS_TTL_SEC:
            return cached

        results = await asyncio.gather(*(check() for check in CHECKS), return_exceptions=True)
        sources: list[Source] = []
        for check, result in zip(CHECKS, results):
            if isinstance(result, Exception):
                sources.append(
                    _result(
                        check.__name__.replace("check_", "").replace("_", " ").title(),
                        "Infrastructure",
                        "offline",
                        str(result),
                    )
                )
            else:
                sources.append(result)

        by_category: dict[str, list[Source]] = {cat: [] for cat in CATEGORY_ORDER}
        for src in sources:
            cat = src.get("category") or "Infrastructure"
            by_category.setdefault(cat, []).append(src)

        categories = [
            {
                "name": cat,
                "sources": by_category.get(cat, []),
                "operational": sum(1 for s in by_category.get(cat, []) if s.get("status") == "operational"),
                "total": len(by_category.get(cat, [])),
            }
            for cat in CATEGORY_ORDER
            if by_category.get(cat)
        ]

        flat = [s for cat in categories for s in cat["sources"]]
        payload = {
            "sources": flat,
            "categories": categories,
            "summary": {
                "total": len(flat),
                "operational": sum(1 for s in flat if s.get("status") == "operational"),
                "degraded": sum(1 for s in flat if s.get("status") in ("degraded", "rate_limited")),
                "offline": sum(1 for s in flat if s.get("status") in ("offline", "no_key")),
            },
            "cached": False,
            "cache_ttl_sec": _STATUS_TTL_SEC,
        }
        _STATUS_CACHE["ts"] = time.time()
        _STATUS_CACHE["payload"] = {**payload, "cached": True}
        return payload
