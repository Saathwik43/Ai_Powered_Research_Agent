"""Source outcomes on SearchMeta — per-database yield for a literature search."""

import asyncio
import time
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from core.auth import get_current_user
from integrations.paper_search import (
    SOURCE_NAMES,
    SearchMeta,
    SourceOutcome,
    _ordered_outcomes,
    _split_fan_out,
    _unpack_cache_entry,
)
import integrations.paper_search as ps
from main import app

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
app.state.limiter.enabled = False
client = TestClient(app)


def test_source_outcome_as_dict_omits_empty_fields():
    ok = SourceOutcome(name="arXiv", status="ok", count=12, ms=410)
    assert ok.as_dict() == {"name": "arXiv", "status": "ok", "count": 12, "ms": 410}

    skipped = SourceOutcome(name="BASE", status="skipped")
    assert skipped.as_dict() == {"name": "BASE", "status": "skipped", "count": 0}


def test_unpack_cache_entry_accepts_legacy_and_current_tuples():
    papers = [{"title": "A"}]
    legacy_papers, stored_at, sources = _unpack_cache_entry((papers, 123.0))
    assert legacy_papers is papers
    assert stored_at == 123.0
    assert sources == ()

    outcome = SourceOutcome(name="arXiv", status="ok", count=1, ms=9)
    cur_papers, cur_at, cur_sources = _unpack_cache_entry((papers, 456.0, (outcome,)))
    assert cur_papers is papers
    assert cur_at == 456.0
    assert cur_sources == (outcome,)


def test_split_fan_out_accepts_stubbed_list():
    papers = [{"title": "A"}]
    got, sources = _split_fan_out(papers)
    assert got == papers
    assert sources == ()

    outcome = SourceOutcome(name="Springer", status="timeout", ms=20000, error="exceeded 20s")
    got, sources = _split_fan_out((papers, (outcome,)))
    assert sources[0].name == "Springer"
    assert sources[0].status == "timeout"


def test_ordered_outcomes_follow_source_names():
    by_name = {
        "DOAJ": SourceOutcome(name="DOAJ", status="ok", count=2),
        "Springer": SourceOutcome(name="Springer", status="timeout"),
        "OpenAlex": SourceOutcome(name="OpenAlex", status="ok", count=20),
    }
    ordered = _ordered_outcomes(by_name)
    assert [o.name for o in ordered] == ["OpenAlex", "Springer", "DOAJ"]
    assert ordered[0].name == SOURCE_NAMES[1]


def test_literature_endpoint_surfaces_source_outcomes():
    papers = [{
        "title": "Graph neural networks for drug discovery",
        "abstract": "A study of GNNs applied to molecular property prediction.",
        "source": "OpenAlex",
        "url": "https://example.com/gnn",
        "year": "2024",
    }]
    meta = SearchMeta(
        cache="miss",
        sources=(
            SourceOutcome(name="OpenAlex", status="ok", count=20, ms=800),
            SourceOutcome(name="CORE", status="timeout", ms=20000, error="exceeded 20s"),
            SourceOutcome(name="BASE", status="empty", count=0, ms=400),
        ),
    )
    with patch("routers.discovery.search_all_with_meta", new=AsyncMock(return_value=(papers, meta))), \
         patch("routers.discovery._filter_relevant_papers", new=AsyncMock(return_value=papers)):
        response = client.get(
            "/api/literature",
            params={"query": "graph neural networks for drug discovery"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0] == {"name": "OpenAlex", "status": "ok", "count": 20, "ms": 800}
    assert body["sources"][1]["status"] == "timeout"
    assert body["sources"][1]["error"] == "exceeded 20s"
    assert body["sources"][2]["status"] == "empty"


def test_execute_search_records_skip_empty_ok_and_error():
    async def run():
        async def ok(_query, limit=20):
            return [{"title": "Hit", "doi": "10.1/hit", "source": "OpenAlex"}]

        async def empty(_query, limit=20):
            return []

        async def boom(_query, limit=20):
            raise RuntimeError("upstream 503")

        ps._cache.clear()
        with patch.object(ps, "s2_search", ok), \
             patch.object(ps, "openalex_search", ok), \
             patch.object(ps, "crossref_search", empty), \
             patch.object(ps, "pubmed_search", boom), \
             patch.object(ps, "arxiv_search", empty), \
             patch.object(ps, "search_github_knowledge", lambda _q: []), \
             patch.object(ps, "springer_search", empty), \
             patch.object(ps, "europepmc_search", empty), \
             patch.object(ps, "doaj_search", empty), \
             patch.object(ps, "openreview_search", empty), \
             patch.object(ps, "acl_search", empty), \
             patch.object(ps, "zenodo_search", empty), \
             patch.object(ps, "_rank_and_rerank", new=AsyncMock(side_effect=lambda q, papers, emb: papers)):
            return await ps._execute_search(
                "graph neural networks",
                limit_per_source=5,
                semantic_rerank=False,
                source_timeout=5.0,
                oa_timeout=1.0,
                exclude_sources={"DOAJ", "Unpaywall"},
                cache_key="test-outcomes",
                bucket_key="",
                query_embedding=None,
            )

    papers, sources = asyncio.run(run())
    by_name = {s.name: s for s in sources}
    assert by_name["DOAJ"].status == "skipped"
    assert by_name["OpenAlex"].status == "ok"
    assert by_name["OpenAlex"].count == 1
    assert by_name["Crossref"].status == "empty"
    assert by_name["PubMed"].status == "error"
    assert "503" in (by_name["PubMed"].error or "")
    assert by_name["OpenAlex"].ms is not None
    assert any(p.get("title") == "Hit" for p in papers)


def test_exact_cache_hit_keeps_source_outcomes():
    papers = [{"title": "Cached", "doi": "10.1/c"}]
    sources = (SourceOutcome(name="arXiv", status="ok", count=8, ms=200),)
    key = f"{ps.canonical_key('machine learning')}_20_True_all"
    ps._cache.clear()
    ps._cache[key] = (papers, time.time(), sources)

    got, meta = asyncio.run(ps.search_all_with_meta("machine learning", limit_per_source=20))
    assert [p["title"] for p in got] == ["Cached"]
    assert meta.cache == "exact"
    assert meta.sources[0].name == "arXiv"
    assert meta.sources[0].count == 8
