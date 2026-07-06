import pytest
from ai.numerical_validator import validate_numerical_claims

def test_valid_number_passes():
    source_papers = [
        {"title": "Solar Cells", "abstract": "We achieved a 28.5% efficiency using a 15 mA/cm² current at 25 °C."}
    ]
    generated_text = "The study reports an impressive 28.5% efficiency, operating at a current of 15 mA/cm² under 25 °C."
    
    result = validate_numerical_claims(generated_text, source_papers)
    assert result["unverified_numbers"] == []

def test_hallucinated_number_caught():
    source_papers = [
        {"title": "Solar Cells", "abstract": "We achieved a 25.1% efficiency."}
    ]
    # 28.5% is hallucinated. 30 nm is hallucinated.
    generated_text = "The researchers claimed a 28.5% efficiency with a 30 nm layer."
    
    result = validate_numerical_claims(generated_text, source_papers)
    assert "28.5%" in result["unverified_numbers"]
    assert "30 nm" in result["unverified_numbers"]
    assert len(result["unverified_numbers"]) == 2

def test_empty_source_papers_skipped():
    source_papers = []
    generated_text = "They achieved 100% efficiency."
    
    result = validate_numerical_claims(generated_text, source_papers)
    assert result["unverified_numbers"] == []

def test_regression_ferroelectric_nematic():
    source_papers = [
        {
            "title": "First-principles study of ferroelectric nematic",
            "abstract": "We calculate a polarization of 6.2 µC/cm² and an efficiency of 14.5 percent in this novel material."
        }
    ]
    # '14.5%' should pass because source has '14.5 percent'.
    # '6.2' bare number near 'polarization' isn't explicitly checked by our keyword list but it is part of a unit if we matched µC/cm².
    # Let's check our keywords. We have 'efficiency' as keyword, so "an efficiency of 99.9" should fail.
    generated_text = "The new material exhibits an efficiency of 14.5% and a record efficiency of 99.9."
    
    result = validate_numerical_claims(generated_text, source_papers)
    
    # 14.5% is valid
    assert "14.5%" not in result["unverified_numbers"]
    
    # 99.9 is hallucinated bare number near 'efficiency'
    assert "99.9" in result["unverified_numbers"]
