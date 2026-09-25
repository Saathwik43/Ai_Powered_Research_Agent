import json
from unittest.mock import patch, AsyncMock

import pytest

from ai import paper_brief


@pytest.fixture(autouse=True)
def clear_brief_cache():
    paper_brief._brief_cache.clear()
    yield
    paper_brief._brief_cache.clear()


@pytest.fixture
def search_time_briefs():
    """Re-enable what conftest turns off for the rest of the suite."""
    paper_brief.set_enabled(True)
    yield
    paper_brief.set_enabled(False)


@pytest.mark.anyio
async def test_brief_paper_success():
    paper = {
        "title": "ARCS: Adaptive Reinforcement for Cyber Response",
        "abstract": (
            "We present ARCS, a reinforcement-learning system that automates "
            "incident response. On a simulated network it cut mean response "
            "time by 37% versus a static playbook. The method uses a PPO agent "
            "trained on historical tickets. It does not yet handle zero-day exploits."
        ),
    }
    mock_json = json.dumps({
        "bullets": [
            "About: A system that uses trial-and-error learning to automate cyber-attack response.",
            "Method: A PPO (policy) agent trained on past incident tickets.",
            "Finding: Automated, adaptive playbooks can replace static response checklists.",
            "Results: Mean response time fell 37% versus a static playbook in simulation.",
            "Limit: Zero-day exploits are out of scope.",
        ]
    })
    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_json
        bullets = await paper_brief.brief_paper(paper)
    assert len(bullets) == 5
    assert bullets[0].startswith("About:")
    assert "37%" in bullets[3]
    mock_gen.assert_called_once()
    again = await paper_brief.brief_paper(paper)
    assert again == bullets
    mock_gen.assert_called_once()


@pytest.mark.anyio
async def test_brief_paper_falls_back_to_extractive():
    paper = {
        "title": "Broken",
        "abstract": "We study X in detail. We use method Y on dataset Z. Results show a 12% gain.",
    }
    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = Exception("LLM Error")
        bullets = await paper_brief.brief_paper(paper)
    assert bullets[0].startswith("About:")
    assert any(b.startswith("Method:") for b in bullets)
    assert len(bullets) >= 2


def test_extractive_brief_splits_sentences():
    bullets = paper_brief.extractive_brief(
        "Membership Inference",
        "We investigate how models leak training data. We focus on membership inference. Accuracy reaches 90%.",
    )
    labels = [b.split(":", 1)[0] for b in bullets]
    assert "About" in labels
    assert "Method" in labels
    assert any("leak training data" in b for b in bullets)
    assert len(bullets) >= 3


def test_extractive_brief_avoids_truncated_paragraph():
    abstract = (
        "We quantitatively investigate how machine learning models leak information "
        "about the individual data records on which they were trained. We focus on "
        "the basic membership inference attack: given a data record and black-box "
        "access to a model, we determine whether this record was used to train it."
    )
    bullets = paper_brief.extractive_brief(
        "Membership Inference Attacks Against Machine Learning Models",
        abstract,
    )
    assert len(bullets) >= 3
    labels = [b.split(":", 1)[0] for b in bullets]
    assert labels[0] == "About"
    assert "Method" in labels
    assert all(":" in b for b in bullets)
    joined = " ".join(bullets)
    assert joined != abstract


def test_clip_brief_keeps_the_whole_sentence():
    sentence = (
        "We characterize the importance of model choice for downstream fairness "
        "outcomes, and we evaluate this effect using multiple datasets, metrics and "
        "demographic slices drawn from several public benchmarks."
    )
    assert 180 < len(sentence) <= 260
    assert paper_brief._clip_brief(sentence) == sentence


def test_clip_brief_marks_a_cut_instead_of_faking_a_full_stop():
    long_sentence = "We characterize the importance of model choice " + ("and more words " * 30)
    clipped = paper_brief._clip_brief(long_sentence)
    assert clipped.endswith("…")
    assert not clipped.endswith("s.")
    assert len(clipped) <= 181


def test_clip_brief_stops_on_a_sentence_boundary():
    text = " ".join(f"Sentence number {n} says something about the work." for n in range(1, 12))
    clipped = paper_brief._clip_brief(text)
    assert len(clipped) <= 260
    assert clipped.endswith(".")
    assert text.startswith(clipped)
    assert not clipped.endswith("Sentence.")


def test_extractive_brief_skips_results_without_an_outcome():
    bullets = paper_brief.extractive_brief(
        "Fairness and model choice",
        "Machine learning models are increasingly used in high-stakes settings. "
        "We evaluate this effect using multiple datasets, metrics and demographic slices.",
    )
    labels = [b.split(":", 1)[0] for b in bullets]
    assert "Results" not in labels
    assert "Method" in labels


def test_extractive_brief_keeps_results_with_an_outcome():
    bullets = paper_brief.extractive_brief(
        "Fairness and model choice",
        "We train a ranker on public data. Our approach improves calibration error by 12%.",
    )
    results = [b for b in bullets if b.startswith("Results:")]
    assert results and "12%" in results[0]


def test_extractive_brief_marks_an_abstract_the_source_cut_short():
    bullets = paper_brief.extractive_brief(
        "Model choice matters",
        "We study fairness in ranking systems. We characterize the importance of "
        "model choice and evaluate the effect using multiple",
    )
    assert any(b.endswith("…") for b in bullets)


def test_extractive_brief_repairs_a_dangling_clause():
    bullets = paper_brief.extractive_brief(
        "Fairness",
        "In this work we characterize the importance of model choice for fairness, "
        "and we release the code.",
    )
    assert not any(b.rstrip().endswith("and") for b in bullets)
    assert all(b.rstrip().endswith((".", "!", "?", "…")) for b in bullets)


def test_parse_bullets_clips_a_long_llm_bullet_on_a_word():
    raw = json.dumps({"bullets": [
        "About: " + ("a very long clause without any terminal punctuation " * 12),
        "Method: short and complete.",
    ]})
    bullets = paper_brief._parse_bullets(raw)
    assert bullets[0].endswith("…")
    assert not bullets[0].endswith("clau…")


@pytest.mark.anyio
async def test_brief_paper_skips_placeholder_abstract():
    paper = {"title": "No abs", "abstract": "No abstract available"}
    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = json.dumps({"bullets": ["About: x", "Method: y", "Finding: z"]})
        bullets = await paper_brief.brief_paper(paper)
    assert bullets == ["About: x", "Method: y", "Finding: z"]
    prompt = mock_gen.call_args.kwargs["user_prompt"]
    assert "(none)" in prompt


@pytest.mark.anyio
async def test_brief_papers_preserves_order():
    papers = [
        {"title": "PaperAlpha", "abstract": "About apples and methods."},
        {"title": "PaperBeta", "abstract": "About bananas and methods."},
    ]

    async def fake_complete(**kwargs):
        title = "PaperAlpha" if "PaperAlpha" in kwargs["user_prompt"] else "PaperBeta"
        return json.dumps({
            "bullets": [f"About: {title}", "Method: experiment", "Finding: claimed"]
        })

    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, side_effect=fake_complete):
        out = await paper_brief.brief_papers(papers)
    assert out[0][0] == "About: PaperAlpha"
    assert out[1][0] == "About: PaperBeta"


@pytest.mark.anyio
async def test_briefs_within_keeps_what_finished_before_the_deadline(search_time_briefs):
    import anyio

    papers = [
        {"title": "Fast", "abstract": "We train a ranker. Accuracy reaches 91%."},
        {"title": "Slow", "abstract": "We train a ranker on public data for a while."},
    ]
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Finding: z"]})

    async def fake_complete(**kwargs):
        if "Slow" in kwargs["user_prompt"]:
            await anyio.sleep(5)
        return bullets

    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, side_effect=fake_complete):
        with patch("ai.paper_brief.shared_store.set_many", new_callable=AsyncMock):
            out = await paper_brief.briefs_within(papers, seconds=0.3)

    assert out[0] == ["About: x", "Method: y", "Finding: z"]
    # The slow paper is absent rather than served as a half-finished briefing —
    # the client asks for it through POST /api/literature/brief.
    assert out[1] is None


@pytest.mark.anyio
async def test_briefs_within_persists_model_output_only(search_time_briefs):
    papers = [
        {"title": "Works", "abstract": "We train a ranker. Accuracy reaches 91%."},
        {"title": "Fails", "abstract": "We train a ranker on public data for a while."},
    ]
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Finding: z"]})

    async def fake_complete(**kwargs):
        if "Fails" in kwargs["user_prompt"]:
            raise RuntimeError("provider down")
        return bullets

    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, side_effect=fake_complete):
        with patch("ai.paper_brief.shared_store.set_many", new_callable=AsyncMock) as mock_set:
            out = await paper_brief.briefs_within(papers, seconds=5)

    assert out[0] == ["About: x", "Method: y", "Finding: z"]
    assert out[1] and out[1][0].startswith("About:")  # extractive fallback still shown
    namespace, stored, ttl = mock_set.call_args.args
    assert namespace == "brief"
    assert ttl == paper_brief._SHARED_TTL
    # Caching the fallback would freeze a degraded briefing in place for hours.
    assert list(stored.values()) == [["About: x", "Method: y", "Finding: z"]]


@pytest.mark.anyio
async def test_a_durable_hit_costs_no_llm_call(search_time_briefs):
    paper = {"title": "Cached", "abstract": "We train a ranker. Accuracy reaches 91%.", "doi": "10.1/abc"}
    stored = {paper_brief._cache_key(paper): ["About: from the store", "Method: y"]}

    with patch("ai.paper_brief.shared_store.enabled", return_value=True), \
         patch("ai.paper_brief.shared_store.get_many", new_callable=AsyncMock, return_value=stored), \
         patch("ai.paper_brief.shared_store.set_many", new_callable=AsyncMock) as mock_set, \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock) as mock_gen:
        out = await paper_brief.brief_papers([paper])

    assert out == [["About: from the store", "Method: y"]]
    mock_gen.assert_not_called()
    mock_set.assert_not_called()


@pytest.mark.anyio
async def test_warm_briefs_swallows_a_provider_failure(search_time_briefs):
    papers = [{"title": "P", "abstract": "We train a ranker on public data for a while."}]
    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, side_effect=RuntimeError("down")):
        with patch("ai.paper_brief.shared_store.set_many", new_callable=AsyncMock) as mock_set:
            assert await paper_brief.warm_briefs(papers) is None
    mock_set.assert_not_called()


@pytest.mark.anyio
async def test_attach_briefs_copies_papers_and_warms_the_tail(search_time_briefs):
    from fastapi import BackgroundTasks
    from routers import discovery

    papers = [
        {"title": f"Paper {n}", "abstract": "We train a ranker. Accuracy reaches 91%."}
        for n in range(paper_brief.INLINE_BRIEF_COUNT + 4)
    ]
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Finding: z"]})
    background = BackgroundTasks()

    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, return_value=bullets):
        with patch("ai.paper_brief.shared_store.set_many", new_callable=AsyncMock):
            out = await discovery._attach_briefs(papers, background)

    assert all("brief" in p for p in out[:paper_brief.INLINE_BRIEF_COUNT])
    assert all("brief" not in p for p in out[paper_brief.INLINE_BRIEF_COUNT:])
    # These dicts are the cached search results — a brief written into them
    # would be served as part of the next cache hit for another query.
    assert all("brief" not in p for p in papers)
    assert len(background.tasks) == 1
    assert background.tasks[0].func is paper_brief.warm_briefs
    assert len(background.tasks[0].args[0]) == 4


def test_literature_response_carries_briefs_for_the_head(search_time_briefs):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from main import app
    from core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
    app.state.limiter.enabled = False
    client = TestClient(app)

    papers = [
        {"title": f"Paper {n}", "abstract": "We train a ranker. Accuracy reaches 91%."}
        for n in range(3)
    ]
    meta = SimpleNamespace(matched_query=None, cache=None, sources=[])
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Finding: z"]})

    with patch("routers.discovery.search_all_with_meta", new_callable=AsyncMock, return_value=(papers, meta)), \
         patch("routers.discovery._collect_relevant", new_callable=AsyncMock, return_value=(papers, 3)), \
         patch("routers.discovery.record_query_shared", new_callable=AsyncMock), \
         patch("ai.paper_brief.shared_store.set_many", new_callable=AsyncMock), \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, return_value=bullets):
        resp = client.get("/api/literature", params={"query": "ranking fairness"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["brief"] == ["About: x", "Method: y", "Finding: z"]


FULL_TEXT_EVIDENCE = {
    "objective": "We study whether model choice changes downstream fairness outcomes.",
    "method": "We train 40 rankers across four architectures and evaluate them on three benchmarks.",
    "dataset": "MSLR-WEB30K, Istella and a proprietary e-commerce log.",
    "results": "Calibration error fell 12% and group disparity narrowed from 0.21 to 0.09.",
    "limitations": "The study covers only binary group labels and English-language queries.",
    "future_work": "Extending to intersectional groups is left to future work.",
}


@pytest.fixture(autouse=True)
def clear_deep_cache():
    paper_brief._deep_cache.clear()
    yield
    paper_brief._deep_cache.clear()


@pytest.mark.anyio
async def test_deep_brief_reads_the_full_text():
    paper = {"title": "Model choice", "abstract": "stub.", "url": "https://arxiv.org/abs/2401.00001"}
    bullets = json.dumps({"bullets": [
        "About: Whether the model you pick changes who the ranking treats unfairly.",
        "Method: 40 rankers over four designs, scored on three public benchmarks.",
        "Results: Calibration error fell 12% and the gap between groups halved.",
        "Limit: Only binary group labels and English queries were covered.",
    ]})

    with patch("ai.evidence_extraction.extract_evidence_for_paper", new_callable=AsyncMock,
               return_value=(FULL_TEXT_EVIDENCE, "arxiv-html")), \
         patch("ai.paper_brief.shared_store.set", new_callable=AsyncMock), \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, return_value=bullets) as mock_gen:
        out = await paper_brief.deep_brief_paper(paper)

    assert out["source"] == "arxiv-html"
    assert any(b.startswith("Results:") and "12%" in b for b in out["bullets"])
    # Sections, not the paper: the whole context is a few hundred words.
    prompt = mock_gen.call_args.kwargs["user_prompt"]
    assert "Results: Calibration error fell 12%" in prompt
    assert len(prompt) < 4000


@pytest.mark.anyio
async def test_deep_brief_falls_back_to_the_abstract_when_nothing_is_readable():
    paper = {"title": "Closed access", "abstract": "We train a ranker. Accuracy reaches 91%."}
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Finding: z"]})

    with patch("ai.evidence_extraction.extract_evidence_for_paper", new_callable=AsyncMock,
               return_value=({}, "llm-fallback")), \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, return_value=bullets):
        out = await paper_brief.deep_brief_paper(paper)

    # `source: abstract` is what tells the card to stop offering the button.
    assert out["source"] == "abstract"
    assert out["bullets"] == ["About: x", "Method: y", "Finding: z"]


@pytest.mark.anyio
async def test_deep_brief_labels_the_sections_when_the_model_fails():
    paper = {"title": "Model choice", "abstract": "stub.", "url": "https://arxiv.org/abs/2401.00001"}

    with patch("ai.evidence_extraction.extract_evidence_for_paper", new_callable=AsyncMock,
               return_value=(FULL_TEXT_EVIDENCE, "europepmc-fulltext")), \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, side_effect=RuntimeError("down")):
        out = await paper_brief.deep_brief_paper(paper)

    # Unpolished, but it is the paper's own Results section rather than a
    # sentence from the abstract that happened to contain a number.
    assert out["source"] == "europepmc-fulltext"
    labels = [b.split(":", 1)[0] for b in out["bullets"]]
    assert labels == ["About", "Method", "Results", "Limit", "Next"]


@pytest.mark.anyio
async def test_deep_brief_caches_bullets_not_bytes():
    paper = {"title": "Model choice", "abstract": "stub.", "doi": "10.1/abc"}
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Results: z"]})

    with patch("ai.evidence_extraction.extract_evidence_for_paper", new_callable=AsyncMock,
               return_value=(FULL_TEXT_EVIDENCE, "arxiv-html")) as mock_extract, \
         patch("ai.paper_brief.shared_store.set", new_callable=AsyncMock) as mock_set, \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, return_value=bullets):
        first = await paper_brief.deep_brief_paper(paper)
        second = await paper_brief.deep_brief_paper(paper)

    assert first == second
    mock_extract.assert_called_once()  # the second call never touched the network
    namespace, key, value, ttl = mock_set.call_args.args
    assert namespace == "brief"
    assert key.startswith("deep:")
    assert set(value) == {"bullets", "source"}
    assert ttl == paper_brief._SHARED_TTL


def test_deep_brief_endpoint():
    from fastapi.testclient import TestClient
    from main import app
    from core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
    app.state.limiter.enabled = False
    client = TestClient(app)
    bullets = json.dumps({"bullets": ["About: x", "Method: y", "Results: z"]})

    with patch("ai.evidence_extraction.extract_evidence_for_paper", new_callable=AsyncMock,
               return_value=(FULL_TEXT_EVIDENCE, "arxiv-html")), \
         patch("ai.paper_brief.shared_store.set", new_callable=AsyncMock), \
         patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, return_value=bullets):
        resp = client.post("/api/literature/deep-brief", json={
            "paper": {"title": "Model choice", "abstract": "stub.",
                      "oa_url": "https://example.org/paper.pdf"},
        })

    assert resp.status_code == 200
    assert resp.json()["source"] == "arxiv-html"
    assert resp.json()["bullets"][0].startswith("About:")


def test_brief_endpoint_preserves_order():
    from fastapi.testclient import TestClient
    from main import app
    from core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
    app.state.limiter.enabled = False
    client = TestClient(app)
    payload = {
        "papers": [
            {"title": "PaperAlpha", "abstract": "We study one method and report a 2x speedup."},
            {"title": "PaperBeta", "abstract": "We study two methods and report a 3x speedup."},
        ]
    }

    async def fake_complete(**kwargs):
        label = "PaperAlpha" if "PaperAlpha" in kwargs["user_prompt"] else "PaperBeta"
        return json.dumps({
            "bullets": [f"About: {label}", "Method: experiment", "Finding: claimed"]
        })

    with patch("ai.paper_brief.generate_completion", new_callable=AsyncMock, side_effect=fake_complete):
        resp = client.post("/api/literature/brief", json=payload)
    assert resp.status_code == 200
    briefs = resp.json()["briefs"]
    assert briefs[0]["bullets"][0] == "About: PaperAlpha"
    assert briefs[1]["bullets"][0] == "About: PaperBeta"

