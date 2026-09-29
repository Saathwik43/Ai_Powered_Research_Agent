"""
test_snowball.py
----------------
Citation snowballing (2.2) — follow citations both ways.

Three layers, tested separately:

  * the two graph fetchers, against fake HTTP — do they ask the right endpoint
    for each direction, and do they survive the records those APIs actually
    return (a reference S2 never resolved comes back as a null paper);
  * the orchestrator — does it merge two graphs and two directions into one
    deduplicated set, keep the provenance that justifies every row, and rank by
    co-citation rather than by keyword match;
  * the endpoint — does it return the expansion without putting it through the
    relevance classifier, which would discard exactly the prior work
    snowballing exists to surface.

Patch-target note
-----------------
`integrations/snowball.py` binds the two fetchers as module attributes and
resolves them by name per call, so the patch target is
`integrations.snowball.s2_related` / `.openalex_related` — the same late-binding
seam `integrations.paper_search.arxiv_search` provides for keyword search.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from integrations import openalex, semanticscholar, snowball
from services import api_telemetry


# ──────────────────────────────────────────────────────────────────────────────
# Fake HTTP
# ──────────────────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class RecordingClient:
    """Answers from *routes*: the first key that is a substring of the URL wins."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}})
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return FakeResponse({}, status_code=404)


@asynccontextmanager
async def _pool(client):
    yield client


def _patched(module, client):
    return patch.object(module, "pooled_client", lambda *a, **k: _pool(client))


# ──────────────────────────────────────────────────────────────────────────────
# OpenAlex fetcher
# ──────────────────────────────────────────────────────────────────────────────

def _work(title, work_id, doi=None, citations=0):
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "publication_year": 2023,
        "cited_by_count": citations,
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "abstract_inverted_index": {"An": [0], "abstract": [1]},
    }


def test_openalex_backward_asks_for_the_works_the_seed_cites():
    client = RecordingClient({"/works": FakeResponse({"results": [_work("Cited work", "W3001")]})})
    seed = {"title": "Seed", "id": "https://openalex.org/W2741809807"}

    with _patched(openalex, client):
        papers = asyncio.run(openalex.fetch_related(seed, "backward", limit=5))

    assert client.calls[0]["params"]["filter"] == "cited_by:W2741809807"
    # No resolve request: the seed already carried its work id.
    assert len(client.calls) == 1
    assert [p["title"] for p in papers] == ["Cited work"]
    assert papers[0]["abstract"] == "An abstract"
    assert papers[0]["source"] == "OpenAlex"


def test_openalex_forward_asks_for_the_works_citing_the_seed():
    client = RecordingClient({"/works": FakeResponse({"results": [_work("Citing work", "W3002")]})})
    seed = {"title": "Seed", "id": "https://openalex.org/W2741809807"}

    with _patched(openalex, client):
        asyncio.run(openalex.fetch_related(seed, "forward", limit=5))

    assert client.calls[0]["params"]["filter"] == "cites:W2741809807"
    # Most-cited first: a seed with 4000 citing works must contribute the
    # influential ones, not an arbitrary page.
    assert client.calls[0]["params"]["sort"] == "cited_by_count:desc"


def test_openalex_resolves_a_doi_only_seed_before_traversing():
    client = RecordingClient({
        "/works/https://doi.org/": FakeResponse({"id": "https://openalex.org/W5550001"}),
        "/works": FakeResponse({"results": [_work("Found", "W3003")]}),
    })
    seed = {"title": "Seed", "doi": "10.1234/seed"}

    with _patched(openalex, client):
        papers = asyncio.run(openalex.fetch_related(seed, "backward", limit=5))

    assert "doi.org/10.1234/seed" in client.calls[0]["url"]
    assert client.calls[1]["params"]["filter"] == "cited_by:W5550001"
    assert [p["title"] for p in papers] == ["Found"]


def test_a_seed_openalex_has_never_heard_of_is_not_a_source_failure():
    """A 404 on resolve is a real answer about the paper, not a broken API.

    Recording it as a failure would drive the circuit breaker open on a healthy
    source just because the seed set contained an unindexed paper.
    """
    client = RecordingClient({"/works/https://doi.org/": FakeResponse({}, status_code=404)})
    before = api_telemetry.snapshot("OpenAlex").get("calls_fail") or 0

    with _patched(openalex, client):
        papers = asyncio.run(openalex.fetch_related({"doi": "10.1/ghost"}, "backward"))

    assert papers == []
    assert (api_telemetry.snapshot("OpenAlex").get("calls_fail") or 0) == before


def test_a_broken_graph_raises_rather_than_answering_with_nothing():
    """Swallowing the failure into [] would make an outage indistinguishable
    from "this seed has no indexed references" in the source panel. The one
    caller catches per request, so raising costs nothing."""
    client = RecordingClient({"/works": FakeResponse({}, status_code=500)})
    seed = {"title": "Seed", "id": "https://openalex.org/W2741809807"}

    with _patched(openalex, client), pytest.raises(httpx.HTTPStatusError):
        asyncio.run(openalex.fetch_related(seed, "backward"))


def test_a_seed_with_no_identifier_costs_no_request():
    client = RecordingClient({})
    with _patched(openalex, client):
        assert asyncio.run(openalex.fetch_related({"title": "Only a title"}, "forward")) == []
    assert client.calls == []


def test_openalex_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        asyncio.run(openalex.fetch_related({"id": "https://openalex.org/W2741809807"}, "sideways"))


# ──────────────────────────────────────────────────────────────────────────────
# Semantic Scholar fetcher
# ──────────────────────────────────────────────────────────────────────────────

def _s2_paper(title, doi=None, citations=0):
    return {
        "paperId": "b" * 40,
        "title": title,
        "authors": [{"name": "A. Author"}],
        "year": 2022,
        "citationCount": citations,
        "abstract": "An abstract.",
        "externalIds": {"DOI": doi} if doi else {},
    }


def test_s2_backward_reads_the_reference_list():
    client = RecordingClient({"/references": FakeResponse({"data": [
        {"isInfluential": True, "citedPaper": _s2_paper("Foundational work", doi="10.1/found")},
    ]})})

    with _patched(semanticscholar, client):
        papers = asyncio.run(
            semanticscholar.fetch_related({"doi": "10.1234/seed"}, "backward", limit=5)
        )

    assert client.calls[0]["url"].endswith("/DOI:10.1234/seed/references")
    assert [p["title"] for p in papers] == ["Foundational work"]
    # The edge-level verdict has to survive onto the paper — it is the only
    # quality signal either graph gives us for free.
    assert papers[0]["influential"] is True


def test_s2_forward_reads_the_citing_papers_and_skips_unresolved_references():
    client = RecordingClient({"/citations": FakeResponse({"data": [
        {"citingPaper": _s2_paper("Later work")},
        # S2 parsed this reference out of a PDF but never matched it to a record.
        {"citingPaper": None},
        {"citingPaper": {"paperId": "c" * 40, "title": None}},
    ]})})

    with _patched(semanticscholar, client):
        papers = asyncio.run(
            semanticscholar.fetch_related({"url": "https://arxiv.org/abs/2301.00001"}, "forward")
        )

    assert client.calls[0]["url"].endswith("/ARXIV:2301.00001/citations")
    assert [p["title"] for p in papers] == ["Later work"]
    assert "influential" not in papers[0]


def test_an_arxiv_paper_is_addressed_by_its_arxiv_id_not_its_datacite_doi():
    """Regression, found against the live API on 2026-09-25.

    S2 does not index arXiv's DataCite DOI: `DOI:10.48550/arxiv.1706.03762`
    returns nothing for a paper that answers under `ARXIV:1706.03762`. Every
    arXiv record we hold carries both forms, so preferring the DOI — which is
    right for a publisher DOI — silently lost every arXiv-only seed.
    """
    arxiv_paper = {
        "title": "Attention Is All You Need",
        "doi": "10.48550/arXiv.1706.03762",
        "url": "https://arxiv.org/abs/1706.03762",
    }
    assert semanticscholar.paper_ref(arxiv_paper) == "ARXIV:1706.03762"

    # A real publisher DOI still wins: the published version's reference list
    # comes from the publisher rather than from a PDF parse.
    published = {"title": "BERT", "doi": "10.18653/v1/N19-1423",
                 "url": "https://arxiv.org/abs/1810.04805"}
    assert semanticscholar.paper_ref(published) == "DOI:10.18653/v1/n19-1423"


def test_s2_404_is_an_answer_not_a_failure():
    client = RecordingClient({"/references": FakeResponse({}, status_code=404)})
    before = api_telemetry.snapshot("Semantic Scholar").get("calls_fail") or 0

    with _patched(semanticscholar, client):
        papers = asyncio.run(semanticscholar.fetch_related({"doi": "10.1/ghost"}, "backward"))

    assert papers == []
    assert (api_telemetry.snapshot("Semantic Scholar").get("calls_fail") or 0) == before


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

SEED_A = {"title": "Ferroelectric Nematic Liquid Crystals", "doi": "10.1/a"}
SEED_B = {"title": "Polar Order in Nematic Phases", "doi": "10.1/b"}
SEED_C = {"title": "Dielectric Response of Polar Nematics", "doi": "10.1/c"}


def _paper(title, doi=None, citations=0, source="Semantic Scholar", **extra):
    out = {
        "title": title,
        "authors": "A. Author",
        "year": "2023",
        "citations": citations,
        "abstract": "An abstract.",
        "source": source,
        "doi": doi or "",
    }
    out.update(extra)
    return out


def _fetcher(table):
    """A fake fetcher answering from ``{(seed doi, direction): [papers]}``."""

    async def fetch(seed, direction, limit=25):
        return [dict(p) for p in table.get((seed.get("doi"), direction), [])]

    return fetch


def _run(seeds, **kwargs):
    return asyncio.run(snowball.snowball(seeds, **kwargs))


def test_both_directions_and_both_graphs_merge_into_one_set():
    s2 = _fetcher({
        ("10.1/a", "backward"): [_paper("Landau Theory of Polar Order", doi="10.9/landau")],
        ("10.1/a", "forward"): [_paper("Devices from Polar Nematics", doi="10.9/devices")],
    })
    # OpenAlex knows the same Landau paper — the classic case for merging two
    # graphs rather than trusting one.
    oa = _fetcher({
        ("10.1/a", "backward"): [
            _paper("Landau Theory of Polar Order", doi="10.9/landau",
                   source="OpenAlex", citations=900),
            _paper("Early Flexoelectric Measurements", doi="10.9/early", source="OpenAlex"),
        ],
    })

    with patch.object(snowball, "s2_related", s2), patch.object(snowball, "openalex_related", oa):
        papers, meta = _run([SEED_A])

    titles = sorted(p["title"] for p in papers)
    assert titles == [
        "Devices from Polar Nematics",
        "Early Flexoelectric Measurements",
        "Landau Theory of Polar Order",
    ]
    landau = next(p for p in papers if p["title"] == "Landau Theory of Polar Order")
    # One card, not two — and the merge kept the richer record's citation count.
    assert landau["citations"] == 900
    assert landau["snowball_direction"] == "backward"

    devices = next(p for p in papers if p["title"] == "Devices from Polar Nematics")
    assert devices["snowball_direction"] == "forward"

    assert meta.cache == "miss"
    assert meta.seeds_used == 1
    assert {o.name: o.status for o in meta.sources} == {
        "SemanticScholar": "ok", "OpenAlex": "ok",
    }


def test_a_paper_reached_both_ways_reports_both():
    """A cites B and B cites A happens: a journal version citing the preprint of
    a paper that cites it back, and any pair of companion papers."""
    s2 = _fetcher({
        ("10.1/a", "backward"): [_paper("Companion Paper", doi="10.9/companion")],
        ("10.1/a", "forward"): [_paper("Companion Paper", doi="10.9/companion")],
    })
    with patch.object(snowball, "s2_related", s2), \
         patch.object(snowball, "openalex_related", _fetcher({})):
        papers, _ = _run([SEED_A])

    assert len(papers) == 1
    assert papers[0]["snowball_direction"] == "both"


def test_the_expansion_never_returns_the_seeds_themselves():
    """The reference lists of papers in one area overlap heavily with the area's
    own papers, so without this the first page is mostly seeds."""
    s2 = _fetcher({
        ("10.1/a", "backward"): [
            # Same paper as SEED_B, arriving with only a title.
            _paper("Polar Order in Nematic Phases"),
            # Same paper as SEED_C, arriving with only its DOI and a variant title.
            _paper("Dielectric response of polar nematics (preprint)", doi="10.1/c"),
            _paper("Genuinely New Work", doi="10.9/new"),
        ],
    })
    with patch.object(snowball, "s2_related", s2), \
         patch.object(snowball, "openalex_related", _fetcher({})):
        papers, _ = _run([SEED_A, SEED_B, SEED_C])

    assert [p["title"] for p in papers] == ["Genuinely New Work"]


def test_co_citation_outranks_keyword_match():
    """The whole point of snowballing: a paper the seed set collectively points
    at is central whether or not its title repeats the query."""
    hub = _paper("Landau Theory", doi="10.9/hub")
    fringe = _paper("Ferroelectric Nematic Liquid Crystals Review", doi="10.9/fringe",
                    citations=5000)
    s2 = _fetcher({
        ("10.1/a", "backward"): [dict(hub)],
        ("10.1/b", "backward"): [dict(hub)],
        ("10.1/c", "backward"): [dict(hub), dict(fringe)],
    })
    with patch.object(snowball, "s2_related", s2), \
         patch.object(snowball, "openalex_related", _fetcher({})):
        papers, _ = _run([SEED_A, SEED_B, SEED_C], query="ferroelectric nematic liquid crystals")

    assert [p["title"] for p in papers] == ["Landau Theory", "Ferroelectric Nematic Liquid Crystals Review"]
    assert papers[0]["snowball_seed_count"] == 3
    assert papers[0]["snowball_seeds"] == [
        "Dielectric Response of Polar Nematics",
        "Ferroelectric Nematic Liquid Crystals",
        "Polar Order in Nematic Phases",
    ]
    assert papers[1]["snowball_seed_count"] == 1


def test_provenance_survives_a_merge_across_graphs():
    """Dedupe merges records field by field and cannot union a set, so a paper
    both graphs returned from different seeds would otherwise report only the
    seeds of whichever copy won the merge."""
    s2 = _fetcher({("10.1/a", "backward"): [_paper("Shared Ancestor", doi="10.9/anc")]})
    oa = _fetcher({("10.1/b", "forward"): [
        _paper("Shared Ancestor", doi="10.9/anc", source="OpenAlex", citations=400),
    ]})
    with patch.object(snowball, "s2_related", s2), patch.object(snowball, "openalex_related", oa):
        papers, _ = _run([SEED_A, SEED_B])

    assert len(papers) == 1
    assert papers[0]["snowball_seed_count"] == 2
    assert papers[0]["snowball_direction"] == "both"


def test_a_failing_graph_is_reported_as_error_and_an_empty_one_as_empty():
    """"Semantic Scholar returned nothing" and "Semantic Scholar is down" are
    different results and the UI has to be able to tell them apart."""

    async def broken(seed, direction, limit=25):
        raise RuntimeError("upstream exploded")

    oa = _fetcher({})  # answers, knows nothing
    with patch.object(snowball, "s2_related", broken), patch.object(snowball, "openalex_related", oa):
        papers, meta = _run([SEED_A])

    assert papers == []
    statuses = {o.name: o.status for o in meta.sources}
    assert statuses == {"SemanticScholar": "error", "OpenAlex": "empty"}
    assert "failed" in next(o for o in meta.sources if o.name == "SemanticScholar").error


def test_seeds_no_graph_could_traverse_are_counted():
    s2 = _fetcher({("10.1/a", "backward"): [_paper("Something", doi="10.9/x")]})
    with patch.object(snowball, "s2_related", s2), \
         patch.object(snowball, "openalex_related", _fetcher({})):
        _, meta = _run([SEED_A, SEED_B, SEED_C])

    assert meta.seeds_used == 3
    # B and C produced no edge in either graph.
    assert meta.seeds_unresolvable == 2


def test_the_seed_set_is_capped_so_one_snowball_cannot_rate_limit_the_deployment():
    seen = []

    async def counting(seed, direction, limit=25):
        seen.append(seed["doi"])
        return []

    seeds = [{"title": f"Paper {i}", "doi": f"10.1/{i}"} for i in range(40)]
    with patch.object(snowball, "s2_related", counting), \
         patch.object(snowball, "openalex_related", counting):
        _, meta = _run(seeds)

    assert meta.seeds_used == snowball.MAX_SEEDS
    # 2 graphs x 2 directions per seed, and nothing beyond the cap.
    assert len(seen) == snowball.MAX_SEEDS * 4
    assert set(seen) == {f"10.1/{i}" for i in range(snowball.MAX_SEEDS)}


def test_one_direction_only_asks_for_that_direction():
    asked = []

    async def recording(seed, direction, limit=25):
        asked.append(direction)
        return []

    with patch.object(snowball, "s2_related", recording), \
         patch.object(snowball, "openalex_related", recording):
        _, meta = _run([SEED_A], direction="forward")

    assert set(asked) == {"forward"}
    assert meta.direction == "forward"


def test_an_unknown_direction_is_rejected_before_any_request():
    with pytest.raises(ValueError):
        _run([SEED_A], direction="upward")


def test_an_outage_is_not_blamed_on_the_seeds():
    """With both circuits open nothing is traversed. Reporting every seed as
    untraceable would point the reader at their own papers for an outage the
    `sources` rows already state."""
    from services import api_health

    api_health.set_enabled(True)
    api_health.reset()
    try:
        for name in ("SemanticScholar", "OpenAlex"):
            for _ in range(20):
                api_health.record(name, ok=False)
        papers, meta = _run([SEED_A, SEED_B])
    finally:
        api_health.reset()
        api_health.set_enabled(False)

    assert papers == []
    assert meta.seeds_unresolvable == 0
    assert {o.status for o in meta.sources} == {"skipped"}
    assert all(o.error for o in meta.sources)


def test_the_cache_key_does_not_depend_on_seed_order():
    forward = snowball._cache_key([SEED_A, SEED_B, SEED_C], ("backward",), 25)
    reversed_order = snowball._cache_key([SEED_C, SEED_A, SEED_B], ("backward",), 25)
    assert forward == reversed_order
    assert forward != snowball._cache_key([SEED_A, SEED_B], ("backward",), 25)
    assert forward != snowball._cache_key([SEED_A, SEED_B, SEED_C], ("forward",), 25)


def test_no_usable_seed_is_an_empty_expansion_not_a_crash():
    papers, meta = _run([{}, {"abstract": "no title, no id"}])
    assert papers == []
    assert meta.seeds_used == 0


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app
    from core.auth import get_current_user

    # Restored, not popped: several other modules in this suite install this
    # override at import time and never remove it, so popping it here breaks
    # whichever of them pytest happens to collect next.
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "snowball_user"}
    app.state.limiter.enabled = False
    try:
        yield TestClient(app)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous


def test_the_endpoint_returns_the_expansion_with_its_provenance(client):
    expansion = [
        _paper("Landau Theory", doi="10.9/hub", snowball_direction="backward",
               snowball_seeds=["Ferroelectric Nematic Liquid Crystals"], snowball_seed_count=1),
    ]
    meta = snowball.SnowballMeta(cache="miss", direction="both", seeds_used=1)

    with patch("routers.discovery.snowball", AsyncMock(return_value=(expansion, meta))) as call:
        response = client.post("/api/literature/snowball", json={
            "seeds": [{"title": "Ferroelectric Nematic Liquid Crystals", "doi": "10.1/a"}],
            "query": "ferroelectric nematics",
        })

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["direction"] == "both"
    assert body["seeds_used"] == 1
    assert body["data"][0]["snowball_seeds"] == ["Ferroelectric Nematic Liquid Crystals"]
    # The seed reached the orchestrator with its DOI intact — without it neither
    # graph is addressable.
    assert call.await_args.args[0][0]["doi"] == "10.1/a"
    assert call.await_args.kwargs["query"] == "ferroelectric nematics"


def test_the_endpoint_does_not_run_the_relevance_classifier(client):
    """A paper four of the user's own seeds cite is relevant by construction,
    and its abstract often does not mention the query at all — classifying an
    expansion throws away precisely what it was run to find."""
    expansion = [_paper("Landau Theory", doi="10.9/hub")]
    meta = snowball.SnowballMeta(cache="miss", direction="both", seeds_used=1)

    with patch("routers.discovery.snowball", AsyncMock(return_value=(expansion, meta))), \
         patch("routers.discovery._filter_relevant_papers", AsyncMock(return_value=[])) as classifier:
        response = client.post("/api/literature/snowball", json={
            "seeds": [{"title": "Seed", "doi": "10.1/a"}],
        })

    assert response.status_code == 200
    assert response.json()["count"] == 1
    classifier.assert_not_awaited()


def test_the_endpoint_rejects_an_unknown_direction_and_an_empty_seed_set(client):
    assert client.post("/api/literature/snowball", json={
        "seeds": [{"title": "Seed", "doi": "10.1/a"}], "direction": "upward",
    }).status_code == 422
    assert client.post("/api/literature/snowball", json={"seeds": []}).status_code == 422


def test_the_endpoint_caps_the_requested_limit(client):
    meta = snowball.SnowballMeta(cache="miss", direction="both", seeds_used=1)
    with patch("routers.discovery.snowball", AsyncMock(return_value=([], meta))) as call:
        client.post("/api/literature/snowball", json={
            "seeds": [{"title": "Seed", "doi": "10.1/a"}], "limit": 5000,
        })
    assert call.await_args.kwargs["limit"] == 60
