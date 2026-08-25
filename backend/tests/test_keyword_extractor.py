"""Regression tests for query-aware topic extraction."""

from ai.keyword_extractor import (
    _query_tokens,
    _term_relatedness,
    _ngrams_for_doc,
    _tokenize,
    extract_top_topics,
)


def test_query_tokens_keep_domain_words():
    assert _query_tokens("data science") == {"data", "science"}
    assert "data" in _query_tokens("big data analytics")


def test_data_science_bigram_survives_academic_stops():
    bigrams, _ = _ngrams_for_doc(_tokenize("Data science pipelines for big data analytics"))
    assert "data science" in bigrams
    assert "big data" in bigrams


def test_partial_science_match_weaker_than_full_phrase():
    q = _query_tokens("data science")
    assert _term_relatedness("data science", q) > _term_relatedness("science education", q)
    assert _term_relatedness("science education", q) < 0.5


def test_extract_prefers_data_science_over_science_education():
    docs = [
        "Data science pipelines for big data analytics and predictive modeling.",
        "Machine learning methods in data science for healthcare outcomes.",
        "Information science and science education curriculum design.",
        "Science approaches to teaching science education in schools.",
        "Big data mining and data science workflows on cloud platforms.",
        "Data visualization and exploratory data analysis in data science.",
    ] * 8
    topics = extract_top_topics(docs, query="data science", top_n=3)
    titles = [t["title"].lower() for t in topics]
    assert titles[0] == "data science"
    assert "science education" not in titles[:2]


def test_cyber_forensics_keeps_domain_phrases():
    docs = [
        "Cyber forensics and machine learning for malware analysis in digital evidence.",
        "Control systems and cyber forensics on ICS networks.",
        "Cyber security digital forensics evidence collection.",
        "Cyber threats malware digital investigation forensics.",
    ] * 10
    topics = extract_top_topics(docs, query="cyber forensics", top_n=3)
    titles = [t["title"].lower() for t in topics]
    assert titles[0] == "cyber forensics"
    assert "machine learning" not in titles
    assert "control systems" not in titles
