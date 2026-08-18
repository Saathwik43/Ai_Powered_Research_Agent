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


# ─── Regressions: the validator must check the corpus the prompt was built from ──


def test_numbers_from_evidence_are_verified():
    """
    _prepare_generation renders each reference from its six-field `evidence`
    dict whenever evidence exists, falling back to `abstract` only when it does
    not. Checking `abstract` alone reported correctly-sourced figures as
    hallucinations -- the common case, since evidence extraction usually succeeds.
    """
    source_papers = [{
        "title": "A study",
        "abstract": "",
        "evidence": {"results": "Accuracy improved by 12.4% and F1 reached 0.87."},
    }]
    generated_text = "The approach improves accuracy by 12.4%, with an F1 score of 0.87."

    result = validate_numerical_claims(generated_text, source_papers)
    assert result["unverified_numbers"] == [], (
        "numbers present in the cited evidence were flagged as hallucinated"
    )


def test_citation_markers_are_not_treated_as_claims():
    """[1] / [2, 3] / [4-6] are reference numbering, not numerical claims."""
    source_papers = [{"title": "A", "abstract": "An accuracy study."}]
    generated_text = "The accuracy trend holds [1], is confirmed in [2, 3], and again in [4-6]."

    result = validate_numerical_claims(generated_text, source_papers)
    assert result["unverified_numbers"] == []


def test_proposed_setup_parameters_are_not_flagged():
    """
    The prompt asks for a PROPOSED methodology and PROJECTED outcomes, so
    hyperparameters are not claims about the literature and have nothing in the
    sources to match. Flagging them punished the model for following the brief.
    """
    source_papers = [{"title": "A", "abstract": "A study of accuracy."}]
    generated_text = (
        "Training is proposed for 100 epochs at a learning rate of 0.001, "
        "with a batch size of 32 and 5-fold cross-validation."
    )

    result = validate_numerical_claims(generated_text, source_papers)
    assert result["unverified_numbers"] == []


def test_hallucination_still_caught_alongside_evidence():
    """Narrowing false positives must not blunt the real signal."""
    source_papers = [{
        "title": "A study",
        "abstract": "",
        "evidence": {"results": "Accuracy improved by 12.4%."},
    }]
    generated_text = "Our method reaches 99.4% accuracy, above the 12.4% baseline [1]."

    result = validate_numerical_claims(generated_text, source_papers)
    assert result["unverified_numbers"] == ["99.4%"]


@pytest.mark.parametrize("paper", [
    {"title": "X", "abstract": None},
    {"title": None, "abstract": None, "evidence": None},
    {"title": "X", "abstract": None, "evidence": {"results": None}},
    {"title": "X", "abstract": None, "text": None},
])
def test_null_fields_do_not_crash(paper):
    """
    Several integrations set "abstract" to a literal JSON null. String
    concatenation on that raised TypeError inside generate_section's try block,
    surfacing to the user as a bogus 503 "AI temporarily unavailable" -- a
    permanent failure misreported as a transient outage.
    """
    result = validate_numerical_claims("Accuracy was 90%.", [paper])
    assert result["unverified_numbers"] == ["90%"]


def test_string_evidence_is_tolerated():
    """Some call sites carry `evidence` as a plain string rather than a dict."""
    source_papers = [{"title": "A", "abstract": "", "evidence": "Accuracy of 42.5% reported."}]
    result = validate_numerical_claims("An accuracy of 42.5% was observed.", source_papers)
    assert result["unverified_numbers"] == []
