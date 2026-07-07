"""
tests/test_gap_analysis.py
--------------------------
Unit tests for the gap analysis endpoint and logic.
"""

import pytest
import uuid
import asyncio
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from auth import get_current_user
from fastapi import HTTPException

# Ensure each test gets a unique rate-limit key so we don't hit the 5/min limit.
@pytest.fixture(autouse=True)
def mock_remote_address():
    user_id = str(uuid.uuid4())
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    with patch("slowapi.util.get_remote_address", return_value=user_id):
        yield

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
        "consensus": [{"claim": "Thing A is known.", "supporting_papers": ["1"]}],
        "conflicts": [{"claim_a": "X", "claim_b": "Y", "papers": ["2","3"], "note": "conflict"}],
        "gaps": [{"description": "Thing C is unknown", "informed_by": ["1","3"]}],
        "suggested_direction": "A highly specific, concrete, detailed, actionable research direction involving methodology X, variables Y, and evaluating outcome Z in a novel context."
    }
    '''

def _mock_gap_analysis_vague(*args, **kwargs):
    """Simulate Gemini returning a vague direction."""
    return '''
    {
        "consensus": [{"claim": "Thing A is known.", "supporting_papers": ["1"]}],
        "conflicts": [],
        "gaps": [{"description": "Thing B is a gap", "informed_by": ["2"]}],
        "suggested_direction": "More research is needed to explore this topic further."
    }
    '''

def _mock_gap_analysis_topic_unclear(*args, **kwargs):
    """Simulate Gemini rejecting nonsense."""
    return '{"error": "topic_unclear"}'

class TestGapAnalysisEndpoint:

    @patch("ai.gap_analysis.extract_evidence_for_paper", new_callable=AsyncMock)
    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_happy_path(self, mock_gen, mock_filter, mock_search, mock_extract):
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_search.return_value = DUMMY_PAPERS
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = _mock_gap_analysis_response

        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert "consensus" in data
        assert "conflicts" in data
        assert "gaps" in data
        assert "suggested_direction" in data
        assert "references" in data
        assert len(data["gaps"]) == 1
        assert "Thing C is unknown" in data["gaps"][0]["description"]
        assert "vagueness_warning" not in data

    @patch("ai.gap_analysis.extract_evidence_for_paper", new_callable=AsyncMock)
    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    def test_gap_analysis_insufficient_literature(self, mock_filter, mock_search, mock_extract):
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_search.return_value = DUMMY_PAPERS[:1] # Only 1 paper
        mock_filter.return_value = DUMMY_PAPERS[:1]
        
        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "insufficient_literature"
        assert data.get("paper_count") == 1

    @patch("ai.gap_analysis.extract_evidence_for_paper", new_callable=AsyncMock)
    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_vagueness_rejection(self, mock_gen, mock_filter, mock_search, mock_extract):
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_search.return_value = DUMMY_PAPERS
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = _mock_gap_analysis_vague # Always return vague

        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("vagueness_warning") is True
        assert mock_gen.call_count == 2 # Initial + 1 retry

    @patch("ai.gap_analysis.extract_evidence_for_paper", new_callable=AsyncMock)
    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_vagueness_rejection_empty_synthesis(self, mock_gen, mock_filter, mock_search, mock_extract):
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_search.return_value = DUMMY_PAPERS # 3 papers
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = lambda *a, **k: '''
        {
            "consensus": [],
            "conflicts": [],
            "gaps": [{"description": "gap", "informed_by": ["1"]}],
            "suggested_direction": "A highly specific, concrete, detailed, actionable research direction involving methodology X, variables Y, and evaluating outcome Z in a novel context."
        }
        '''

        response = client.post("/api/gap-analysis", json={"topic": "ferroelectric nematic liquid crystal"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("vagueness_warning") is True
        assert mock_gen.call_count == 2 # Initial + 1 retry

    def test_gap_analysis_guardrails(self):
        # Layer A/B should catch this before LLM
        response = client.post("/api/gap-analysis", json={"topic": "hrthwrtajarj"})
        assert response.status_code == 400
        
    @patch("ai.gap_analysis.extract_evidence_for_paper", new_callable=AsyncMock)
    @patch("ai.gap_analysis.search_all", new_callable=AsyncMock)
    @patch("ai.gap_analysis._filter_relevant_papers", new_callable=AsyncMock)
    @patch("ai.gap_analysis.generate_completion", new_callable=AsyncMock)
    def test_gap_analysis_topic_unclear_layer_c(self, mock_gen, mock_filter, mock_search, mock_extract):
        from ai.gap_analysis import analyze_gaps

        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_search.return_value = DUMMY_PAPERS
        mock_filter.return_value = DUMMY_PAPERS
        mock_gen.side_effect = _mock_gap_analysis_topic_unclear

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(analyze_gaps("banana pencil submarine"))

        assert exc_info.value.status_code == 400
        assert "unclear" in str(exc_info.value.detail).lower()
