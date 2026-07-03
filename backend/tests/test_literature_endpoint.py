"""
test_literature_endpoint.py
---------------------------
Regression test: the GET /api/literature endpoint must apply
_filter_relevant_papers to its results, returning the SAME subset that
manuscript generation would produce for the same query + paper fixture.

Fixture: 16 ferroelectric nematic papers (13 relevant, 3 cross-domain
irrelevant).  Excluded paper titles are asserted by name to make the
"same 3 papers excluded" requirement explicit.

Patch-target note
-----------------
main.py does `from integrations.paper_search import search_all` and
`from ai.relevance import _filter_relevant_papers` at module-import time.
Those names are bound in the `main` module namespace.  To intercept them we
must patch `main.search_all` (not `integrations.paper_search.search_all`).
The generate_completion call happens *inside* ai/relevance.py, so the correct
patch target for the classifier is `ai.relevance.generate_completion`.
"""

import unittest
from unittest.mock import patch, AsyncMock
import integrations.paper_search as _ps_module  # for cache clearing

from fastapi.testclient import TestClient
import main as main_module
from main import app
from auth import get_current_user

# Override auth for all tests in this module
app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
app.state.limiter.enabled = False

client = TestClient(app)

# ──────────────────────────────────────────────────────────────────────────────
# 16-paper ferroelectric nematic fixture
# 3 papers are cross-domain irrelevant (should be excluded by the filter).
# ──────────────────────────────────────────────────────────────────────────────
IRRELEVANT_TITLES = [
    "Gravitational Wave Detection via LIGO Interferometry",
    "Dark Matter Direct Detection with LUX-ZEPLIN",
    "Higgs Boson Mass Measurement at the LHC Collider",
]

RELEVANT_TITLES = [
    "Ferroelectric Nematic Liquid Crystals: Spontaneous Polarization",
    "Electroviscous Effects in Polar Nematic Phases",
    "Dielectric Properties of Ferroelectric Liquid Crystal Films",
    "Soft-Mode Dynamics near Ferroelectric Nematic Transitions",
    "Polar Order and Electroviscosity in Nematic Phases",
    "High-Permittivity Nematic Liquid Crystals for Electro-Optics",
    "Ferroelectric Phase Transitions in Liquid Crystal Systems",
    "Viscosity Measurements in Splay-Flexoelectric Nematic Phases",
    "Chiral Symmetry Breaking in Ferroelectric Liquid Crystals",
    "Optical Switching in Ferroelectric Nematic Devices",
    "Domain Dynamics in Ferroelectric Nematic Phases",
    "Spontaneous Birefringence in Polar Liquid Crystal Films",
    "Electrokinetic Effects in Ferroelectric Nematic Suspensions",
]

assert len(RELEVANT_TITLES) == 13
assert len(IRRELEVANT_TITLES) == 3

FERROELECTRIC_FIXTURE: list = []
for _t in RELEVANT_TITLES:
    FERROELECTRIC_FIXTURE.append({
        "title": _t,
        "authors": "Test Author et al.",
        "year": "2024",
        "abstract": f"A study on ferroelectric nematic liquid crystals: {_t.lower()}.",
        "url": f"https://example.com/{_t[:20].replace(' ', '_')}",
        "source": "Semantic Scholar",
    })
for _t in IRRELEVANT_TITLES:
    FERROELECTRIC_FIXTURE.append({
        "title": _t,
        "authors": "Other Author et al.",
        "year": "2023",
        "abstract": f"Unrelated physics paper: {_t.lower()}.",
        "url": f"https://example.com/{_t[:20].replace(' ', '_')}",
        "source": "arXiv",
    })

assert len(FERROELECTRIC_FIXTURE) == 16


def _mock_classifier(system_prompt, user_prompt, max_tokens=5, temperature=0.0):
    """Return 'no' for cross-domain irrelevant papers, 'yes' for all others."""
    irrelevant_kws = [
        "gravitational wave", "ligo", "dark matter", "lux-zeplin",
        "higgs boson", "lhc", "collider",
    ]
    prompt_lower = user_prompt.lower()
    if any(kw in prompt_lower for kw in irrelevant_kws):
        return "no"
    return "yes"


import ai.relevance as _relevance_module

def _clear_search_cache():
    """Clear the 10-minute search_all and relevance caches between tests to prevent stale data."""
    _ps_module._cache.clear()
    _relevance_module._relevance_cache.clear()


class TestLiteratureEndpointRelevanceFilter(unittest.IsolatedAsyncioTestCase):
    """
    Verify that GET /api/literature applies relevance filtering and that
    the same 3 cross-domain papers excluded in the manuscript generation
    test are also excluded here.
    """

    def setUp(self):
        _clear_search_cache()

    # Correct patch targets:
    #   main.search_all              — bound name in main.py (from ... import search_all)
    #   ai.relevance.generate_completion — called inside _filter_relevant_papers
    @patch("main.search_all", new_callable=AsyncMock)
    @patch("ai.relevance.generate_completion", new_callable=AsyncMock)
    def test_returns_13_relevant_papers(self, mock_gen, mock_search):
        """
        With 16-paper fixture: 13 relevant + 3 cross-domain irrelevant.
        Endpoint must return exactly 13 papers.
        """
        mock_search.return_value = FERROELECTRIC_FIXTURE
        mock_gen.side_effect = _mock_classifier

        resp = client.get(
            "/api/literature",
            params={"query": "ferroelectric nematic liquid crystal electroviscosity", "limit": 16},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            data["count"],
            13,
            f"Expected 13 papers after filter, got {data['count']}. "
            f"Titles returned: {[p['title'] for p in data['data']]}",
        )

    @patch("main.search_all", new_callable=AsyncMock)
    @patch("ai.relevance.generate_completion", new_callable=AsyncMock)
    def test_excluded_papers_are_the_three_cross_domain(self, mock_gen, mock_search):
        """
        The exact 3 excluded titles must match the cross-domain irrelevant ones —
        not just 'some 3' but these specific ones by name.
        """
        mock_search.return_value = FERROELECTRIC_FIXTURE
        mock_gen.side_effect = _mock_classifier

        resp = client.get(
            "/api/literature",
            params={"query": "ferroelectric nematic liquid crystal electroviscosity", "limit": 16},
        )
        self.assertEqual(resp.status_code, 200)
        returned_titles = {p["title"] for p in resp.json()["data"]}

        for excluded_title in IRRELEVANT_TITLES:
            self.assertNotIn(
                excluded_title,
                returned_titles,
                f"Cross-domain paper leaked through filter: '{excluded_title}'",
            )

        for relevant_title in RELEVANT_TITLES:
            self.assertIn(
                relevant_title,
                returned_titles,
                f"Relevant paper was incorrectly excluded: '{relevant_title}'",
            )

    @patch("main.search_all", new_callable=AsyncMock)
    @patch("ai.relevance.generate_completion", new_callable=AsyncMock)
    def test_response_shape_preserved(self, mock_gen, mock_search):
        """Response shape {'data': [...], 'count': N} must be unchanged."""
        mock_search.return_value = FERROELECTRIC_FIXTURE
        mock_gen.side_effect = _mock_classifier

        resp = client.get(
            "/api/literature",
            params={"query": "ferroelectric nematic liquid crystal electroviscosity"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("data", body)
        self.assertIn("count", body)
        self.assertIsInstance(body["data"], list)
        self.assertEqual(body["count"], len(body["data"]))

    @patch("main.search_all", new_callable=AsyncMock)
    @patch("ai.relevance.generate_completion", new_callable=AsyncMock)
    def test_filter_uses_ai_relevance_not_manuscript_generation(self, mock_gen, mock_search):
        """
        Patch target MUST be ai.relevance.generate_completion — not
        ai.manuscript_generation.generate_completion.  If mock_gen.call_count
        is 0, either search_all returned [] or the wrong module was patched.
        16 papers with no relevance_score each trigger one LLM call.
        """
        mock_search.return_value = FERROELECTRIC_FIXTURE
        mock_gen.side_effect = _mock_classifier

        resp = client.get(
            "/api/literature",
            params={"query": "ferroelectric nematic liquid crystal electroviscosity", "limit": 16},
        )
        self.assertEqual(resp.status_code, 200)
        # 16 papers, all missing relevance_score → 16 generate_completion calls
        self.assertEqual(
            mock_gen.call_count,
            16,
            f"Expected 16 generate_completion calls (one per paper), got {mock_gen.call_count}. "
            "If 0: either main.search_all patch didn't fire or wrong generate_completion target.",
        )


class TestLiteratureEndpointFailOpen(unittest.IsolatedAsyncioTestCase):
    """Verify fail-open: when generate_completion raises, all papers are returned."""

    def setUp(self):
        _clear_search_cache()

    @patch("main.search_all", new_callable=AsyncMock)
    @patch("ai.relevance.generate_completion", new_callable=AsyncMock)
    def test_classifier_failure_returns_all_papers(self, mock_gen, mock_search):
        """When Groq is down, all papers pass through (fail-open)."""
        mock_search.return_value = FERROELECTRIC_FIXTURE
        mock_gen.side_effect = Exception("Groq rate limit")

        resp = client.get(
            "/api/literature",
            params={"query": "ferroelectric nematic liquid crystal electroviscosity", "limit": 16},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            data["count"],
            16,
            "Fail-open: all 16 papers should be returned when classifier is down.",
        )


if __name__ == "__main__":
    unittest.main()
