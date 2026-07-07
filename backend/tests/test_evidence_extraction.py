import pytest
import json
from unittest.mock import patch, AsyncMock
from ai.evidence_extraction import extract_evidence, _evidence_cache

@pytest.fixture(autouse=True)
def clear_cache():
    _evidence_cache.clear()
    yield
    _evidence_cache.clear()

@pytest.mark.anyio
async def test_extract_evidence_success():
    paper = {
        "title": "Quantum Error Correction",
        "abstract": "We present a surface code objective. Our method uses topological qubits. The dataset contains 10k samples. Results show 99% fidelity. Limitations include low temperature requirements."
    }
    
    mock_json = json.dumps({
        "objective": "present a surface code objective",
        "method": "topological qubits",
        "dataset": "10k samples",
        "results": "99% fidelity",
        "limitations": "low temperature",
        "future_work": ""
    })
    
    with patch("ai.evidence_extraction.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_json
        
        result = await extract_evidence(paper)
        
        assert result["objective"] == "present a surface code objective"
        assert result["results"] == "99% fidelity"
        assert result["future_work"] == ""
        mock_gen.assert_called_once()
        
        assert len(_evidence_cache) == 1

@pytest.mark.anyio
async def test_extract_evidence_failure_fail_open():
    paper = {
        "title": "Broken Extraction",
        "abstract": "This should fail open."
    }
    
    # Mock LLM raising an exception
    with patch("ai.evidence_extraction.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = Exception("LLM Error")
        
        result = await extract_evidence(paper)
        
        # Should return default empty structure
        assert result["objective"] == ""
        assert result["method"] == ""
        assert result["results"] == ""
        
        # Cache shouldn't be populated for failures
        assert len(_evidence_cache) == 0

@pytest.mark.anyio
async def test_extract_evidence_invalid_json():
    paper = {
        "title": "Invalid JSON",
        "abstract": "This returns broken JSON."
    }
    
    with patch("ai.evidence_extraction.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "Here is the extraction: { oops not json"
        
        result = await extract_evidence(paper)
        
        assert result["objective"] == ""
        assert result["results"] == ""
        
@pytest.mark.anyio
async def test_extract_evidence_cache():
    paper = {
        "title": "Cached Paper Title",
        "abstract": "Abstract text"
    }
    
    mock_json = json.dumps({
        "objective": "obj1",
        "method": "meth1",
        "dataset": "",
        "results": "",
        "limitations": "",
        "future_work": ""
    })
    
    with patch("ai.evidence_extraction.generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_json
        
        # First call
        r1 = await extract_evidence(paper)
        assert r1["objective"] == "obj1"
        assert mock_gen.call_count == 1
        
        # Second call (should hit cache)
        r2 = await extract_evidence(paper)
        assert r2["objective"] == "obj1"
        assert mock_gen.call_count == 1
