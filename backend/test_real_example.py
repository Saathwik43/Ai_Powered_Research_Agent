import asyncio
from ai.gap_analysis import analyze_gaps
import json
import logging
from unittest.mock import patch

logging.basicConfig(level=logging.INFO)

async def main():
    topic = "ferroelectric nematic liquid crystal"
    print(f"Running gap analysis for topic: {topic}")
    
    # Mock generate_completion to return a realistic response since APIs are unconfigured
    async def mock_generate(*args, **kwargs):
        return """
        {
          "consensus": [
            {
              "claim": "Ferroelectric nematic (NF) phase exhibits spontaneous polarization and strong nonlinear optical response.",
              "supporting_papers": ["1", "3", "5"]
            }
          ],
          "conflicts": [
            {
              "claim_a": "AC fields suppress the NF phase stability.",
              "claim_b": "High-frequency AC fields enhance orientational order.",
              "papers": ["2", "4"],
              "note": "Disagreement might stem from different frequency regimes."
            }
          ],
          "gaps": [
            {
              "description": "Electroviscous coupling under low-frequency AC fields remains poorly characterized.",
              "informed_by": ["1", "4"]
            }
          ],
          "suggested_direction": "Investigate the precise electroviscous coupling mechanisms under sub-100Hz AC fields using a combination of dielectric spectroscopy and rheometry on RM734 mixtures, to resolve the conflicting stability observations."
        }
        """
        
    # Mock extract_evidence to avoid slow fallbacks
    async def mock_extract(p):
        return {
            "objective": "Study the electroviscous effect in NF phase.",
            "method": "Dielectric spectroscopy.",
            "dataset": "",
            "results": "Found strong coupling at low frequencies.",
            "limitations": "Limited frequency range.",
            "future_work": "Test higher frequencies."
        }
        
    with patch("ai.gap_analysis.generate_completion", new=mock_generate), patch("ai.gap_analysis.extract_evidence", new=mock_extract):
        result = await analyze_gaps(topic=topic)
    
    print("\n\n--- RESULTS ---")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
