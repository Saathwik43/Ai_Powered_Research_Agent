"""
tests/test_gap_analysis.py
--------------------------
Unit tests for the gap analysis endpoint and logic.
"""

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
client = TestClient(app)

# Dummy fixture of 3 papers
DUMMY_PAPERS = [
    {"title": "Paper 1", "authors": "A", "year": "2020", "abstract": "Abstract 1"},
    {"title": "Paper 2", "authors": "B", "year": "2021", "abstract": "Abstract 2"},
    {"title": "Paper 3", "authors": "C", "year": "2022", "abstract": "Abstract 3"},
]

def _mock_gap_analysis_response(*args, **kwargs):
    """Simulate Gemini returning valid structured JSON."""
    return '''
    {
        "well_covered": ["Thing A is known [1].", "Thing B is known [2]."],
        "gaps": ["Thing C is unknown despite [1],[3]."],
        "suggested_direction": "A highly specific, concrete, detailed, actionable research direction involving methodology X, variables Y, and evaluating outcome Z in a novel context."
    }
    '''

def _mock_gap_analysis_vague(*args, **kwargs):
    """Simulate Gemini returning a vague direction."""
    return '''
    {
        "well_covered": ["Thing A is known [1]."],
        "gaps": ["Thing B is a gap [2]."],
        "suggested_direction": "More research is needed to explore this topic further."
    }
    '''

def _mock_gap_analysis_topic_unclear(*args, **kwargs):
    """Simulate Gemini rejecting nonsense."""
    return '{"error": "topic_unclear"}'

class TestGapAnalysisEndpoint:

    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_happy_path(self, mock_gen, mock_filter, mock_search):
        mock_search.return_value = DUMMY_PAPERS
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = _mock_gap_analysis_response

        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert "well_covered" in data
        assert "gaps" in data
        assert "suggested_direction" in data
        assert "references" in data
        assert len(data["gaps"]) == 1
        assert "Thing C is unknown" in data["gaps"][0]
        assert "vagueness_warning" not in data

    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    def test_gap_analysis_insufficient_literature(self, mock_filter, mock_search):
        mock_search.return_value = DUMMY_PAPERS[:1] # Only 1 paper
        mock_filter.return_value = DUMMY_PAPERS[:1]
        
        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "insufficient_literature"
        assert data.get("paper_count") == 1

    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_vagueness_rejection(self, mock_gen, mock_filter, mock_search):
        mock_search.return_value = DUMMY_PAPERS
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = _mock_gap_analysis_vague # Always return vague

        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("vagueness_warning") is True
        assert mock_gen.call_count == 2 # Initial + 1 retry

    def test_gap_analysis_guardrails(self):
        # Layer A/B should catch this before LLM
        response = client.post("/api/gap-analysis", json={"topic": "hrthwrtajarj"})
        assert response.status_code == 400
        
    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_topic_unclear_layer_c(self, mock_gen, mock_filter, mock_search):
        mock_search.return_value = DUMMY_PAPERS
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = _mock_gap_analysis_topic_unclear
        
        response = client.post("/api/gap-analysis", json={"topic": "banana pencil submarine"})
        assert response.status_code == 400
        assert "unclear" in response.json().get("detail", "").lower()
