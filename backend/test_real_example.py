import asyncio
from ai.manuscript_generation import generate_section
import json
import logging
from unittest.mock import patch, MagicMock

logging.basicConfig(level=logging.INFO)

async def main():
    topic = "ferroelectric nematic liquid crystal"
    print(f"Running lit_review generation for topic: {topic}")
    
    # Mock search_all to return a few papers
    async def mock_search(*args, **kwargs):
        return [{"id": "1", "title": "Paper 1"}, {"id": "2", "title": "Paper 2"}, {"id": "3", "title": "Paper 3"}]
        
    # Mock _filter_relevant_papers
    async def mock_filter(*args, **kwargs):
        return args[1]
        
    # Mock extract_evidence
    async def mock_extract(p):
        return {
            "objective": "Study the electroviscous effect in NF phase.",
            "method": "Dielectric spectroscopy.",
            "results": "Found strong coupling at low frequencies."
        }

    # Mock analyze_gaps to return a realistic response since APIs are unconfigured
    async def mock_analyze_gaps(*args, **kwargs):
        return {
          "status": "success",
          "consensus": [
            {
              "claim": "Ferroelectric nematic (NF) phase exhibits spontaneous polarization.",
              "supporting_papers": ["1", "3"]
            }
          ],
          "conflicts": [
            {
              "claim_a": "AC fields suppress the NF phase stability.",
              "claim_b": "High-frequency AC fields enhance orientational order.",
              "papers": ["2", "3"],
              "note": "Disagreement might stem from different frequency regimes."
            }
          ],
          "gaps": [
            {
              "description": "Electroviscous coupling under low-frequency AC fields remains poorly characterized.",
              "informed_by": ["1", "2"]
            }
          ],
          "suggested_direction": "Investigate the precise electroviscous coupling mechanisms..."
        }
        
    # Mock the LLM generation call to avoid needing an API key
    async def mock_generate(*args, **kwargs):
        return "This is a mocked generated lit review section."
        
    # Mock citation checking
    async def mock_citation_grounding(*args, **kwargs):
        return {}

    with patch("ai.manuscript_generation.search_all", new=mock_search), \
         patch("ai.manuscript_generation._filter_relevant_papers", new=mock_filter), \
         patch("ai.manuscript_generation.extract_evidence", new=mock_extract), \
         patch("ai.gap_analysis.analyze_gaps", new=mock_analyze_gaps), \
         patch("ai.manuscript_generation.generate_completion", new=mock_generate), \
         patch("ai.manuscript_generation.check_citation_grounding", new=mock_citation_grounding):
        content, flags = await generate_section(topic=topic, section="lit_review", context="")
    
    print("\n\n--- RESULTS ---")
    print("Content:", content)
    print("Flags gap_analysis data:", json.dumps(flags.get("gap_analysis"), indent=2))
    assert flags.get("gap_analysis") is not None, "gap_analysis_data is None in flags!"
    assert "consensus" in flags["gap_analysis"], "Missing consensus!"

if __name__ == "__main__":
    asyncio.run(main())

