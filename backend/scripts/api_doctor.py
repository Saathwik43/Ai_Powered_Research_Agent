"""Live diagnostic for every third-party API the backend talks to.

Run from the backend/ directory:  python scripts/api_doctor.py
Reports HTTP status, latency, and the number of usable records each provider
returns for a fixed probe query, so a 200-with-zero-results provider is not
mistaken for a healthy one.
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

QUERY = "graph neural networks for drug discovery"
rows = []


def rec(name, ok, code, ms, count, note=""):
    rows.append({
        "name": name,
        "ok": ok,
        "code": code,
        "ms": ms,
        "count": count,
        "note": note[:300],
    })


async def probe(name, method, url, *, headers=None, params=None, json_body=None,
                count_fn=None, timeout=20.0):
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.request(method, url, headers=headers or {}, params=params, json=json_body)
        ms = round((time.time() - t0) * 1000)
        count = None
        note = ""
        if count_fn:
            try:
                count = count_fn(r)
            except Exception as e:
                note = f"parse error: {type(e).__name__}: {e}"
        if r.status_code >= 400:
            note = (note + " | " + r.text[:200]).strip(" |")
        rec(name, r.status_code < 400 and (count is None or count > 0), r.status_code, ms, count, note)
    except Exception as e:
        rec(name, False, None, round((time.time() - t0) * 1000), None, f"{type(e).__name__}: {e}")


def jlen(*path):
    def f(r):
        d = r.json()
        for p in path:
            d = d.get(p, {}) if isinstance(d, dict) else {}
        return len(d) if isinstance(d, (list, dict)) else 0
    return f


async def main():
    mailto = os.getenv("CROSSREF_MAILTO", "")

    tasks = [
        # ---------- Literature sources (as called by integrations/) ----------
        probe("OpenAlex", "GET", "https://api.openalex.org/works",
              params={"search": QUERY, "per-page": 15, **({"mailto": mailto} if mailto else {})},
              headers={"User-Agent": f"ResearchAgent/1.0 (mailto:{mailto})"},
              count_fn=jlen("results")),

        probe("SemanticScholar", "GET", "https://api.semanticscholar.org/graph/v1/paper/search",
              params={"query": QUERY, "limit": 15,
                      "fields": "title,authors,year,citationCount,abstract,url,openAccessPdf"},
              headers={"x-api-key": os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")},
              count_fn=jlen("data")),

        probe("Crossref", "GET", "https://api.crossref.org/works",
              params={"query": QUERY, "rows": 15},
              headers={"User-Agent": f"ResearchAgent/1.0 (mailto:{mailto})"},
              count_fn=jlen("message", "items"), timeout=5.0),

        probe("arXiv", "GET", "https://export.arxiv.org/api/query",
              params={"search_query": f"all:{QUERY}", "start": 0, "max_results": 15,
                      "sortBy": "relevance", "sortOrder": "descending"},
              count_fn=lambda r: r.text.count("<entry>")),

        probe("PubMed esearch", "GET",
              "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
              params={"db": "pubmed", "retmode": "json", "term": QUERY, "retmax": 15,
                      "sort": "relevance", "api_key": os.getenv("PUBMED_API_KEY", "")},
              count_fn=lambda r: len(r.json().get("esearchresult", {}).get("idlist", [])),
              timeout=5.0),

        probe("Springer meta/v2", "GET", "https://api.springernature.com/meta/v2/json",
              params={"q": f"keyword:{QUERY}", "api_key": os.getenv("SPRINGER_META_API_KEY", ""), "p": 15},
              count_fn=jlen("records")),



        probe("EuropePMC", "GET", "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
              params={"query": QUERY, "format": "json", "pageSize": 15, "resultType": "core"},
              count_fn=lambda r: len(r.json().get("resultList", {}).get("result", []))),

        probe("DOAJ", "GET",
              f"https://doaj.org/api/search/articles/{urllib.parse.quote(QUERY)}",
              params={"pageSize": 15},
              count_fn=jlen("results")),

        probe("Unpaywall", "GET", "https://api.unpaywall.org/v2/10.1038/nature12373",
              params={"email": mailto},
              count_fn=lambda r: 1 if r.json().get("best_oa_location") else 0),

        # ---------- LLM providers ----------
        probe("Groq /models", "GET", "https://api.groq.com/openai/v1/models",
              headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"},
              count_fn=jlen("data")),

        probe("Gemini /models", "GET", "https://generativelanguage.googleapis.com/v1beta/models",
              params={"key": os.getenv("GEMINI_API_KEY", "")},
              count_fn=jlen("models")),

        probe("OpenAI /models", "GET", "https://api.openai.com/v1/models",
              headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"},
              count_fn=jlen("data")),

        probe("Mistral /models", "GET", "https://api.mistral.ai/v1/models",
              headers={"Authorization": f"Bearer {os.getenv('MISTRAL_API_KEY', '')}"},
              count_fn=jlen("data")),

        probe("OpenRouter /models", "GET", "https://openrouter.ai/api/v1/models",
              headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}"},
              count_fn=jlen("data")),

        probe("OpenRouter /key", "GET", "https://openrouter.ai/api/v1/auth/key",
              headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}"},
              count_fn=lambda r: 1),

        probe("NVIDIA NIM /models", "GET", "https://integrate.api.nvidia.com/v1/models",
              headers={"Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}"},
              count_fn=jlen("data")),

        probe("Cerebras /models", "GET", "https://api.cerebras.ai/v1/models",
              headers={"Authorization": f"Bearer {os.getenv('CEREBRAS_API_KEY', '')}"},
              count_fn=jlen("data")),

        probe("HuggingFace whoami", "GET", "https://huggingface.co/api/whoami-v2",
              headers={"Authorization": f"Bearer {os.getenv('HUGGINGFACEHUB_API_TOKEN') or os.getenv('HF_TOKEN', '')}"},
              count_fn=lambda r: 1),

        # ---------- Document processing / infra ----------
        # GROBID probe removed: every free hosted instance is down (the HF Space
        # returns 503) and PDF structure parsing now runs in-process via
        # PyMuPDF, so there is nothing remote left to probe on that path.

        probe("LlamaCloud", "GET", "https://api.cloud.llamaindex.ai/api/v1/parsing/supported_file_extensions",
              headers={"Authorization": f"Bearer {os.getenv('LLAMA_CLOUD_API_KEY', '')}"},
              count_fn=lambda r: 1),

        probe("Brevo account", "GET", "https://api.brevo.com/v3/account",
              headers={"api-key": os.getenv("BREVO_API_KEY", ""), "accept": "application/json"},
              count_fn=lambda r: 1),
    ]

    await asyncio.gather(*tasks)

    # Configured model names actually resolvable?
    print("\n" + "=" * 100)
    print(f"{'PROVIDER':<26}{'OK':<5}{'HTTP':<7}{'ms':<8}{'n':<6}NOTE")
    print("=" * 100)
    for r in sorted(rows, key=lambda x: (x["ok"], x["name"])):
        flag = "OK " if r["ok"] else "FAIL"
        print(f"{r['name']:<26}{flag:<5}{str(r['code']):<7}{str(r['ms']):<8}{str(r['count']):<6}{r['note']}")
    print("=" * 100)

    with open(os.path.join(os.path.dirname(__file__), "api_doctor_report.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
