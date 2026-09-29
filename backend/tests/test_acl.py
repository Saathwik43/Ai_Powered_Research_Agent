"""ACL Anthology bibliography parsing and title search."""

from integrations.acl import build_index, parse_bibliography, search_index

BIB = """
@proceedings{wildre-2026-1,
    title = "Proceedings of the Workshop",
    editor = "Jha, Girish Nath",
    year = "2026",
    url = "https://aclanthology.org/2026.wildre-1.0/"
}
@inproceedings{kranti-vajjala-2026-metricalargs,
    title = "{M}etrical{ARGS}: Studying Metrical Poetry with {LLM}s",
    author = "Kranti, Chalamalasetti  and
      Vajjala, Sowmya  and
      Doe, Jane  and
      Roe, Richard",
    booktitle = "Proceedings of the 8th Workshop on {I}ndian Language Data: Resources and Evaluation",
    year = "2026",
    publisher = "ELRA Language Resources Association (ELRA)",
    url = "https://aclanthology.org/2026.wildre-1.1/",
    doi = "10.63317/4o3nvxw6qwso",
    pages = "1--13"
}
@article{smith-2020-parsing,
    title = "A Journal Piece on Parsing",
    author = "Smith, Ann",
    journal = "Computational Linguistics",
    year = "2020",
    url = "https://aclanthology.org/2020.cl-1.1/",
    doi = "10.1162/coli_a_00001"
}
"""


def test_papers_keep_venue_doi_and_pdf_and_volumes_are_dropped():
    papers = parse_bibliography(BIB)
    assert len(papers) == 2
    paper = papers[0]
    assert paper["source"] == "ACLAnthology"
    assert paper["title"] == "MetricalARGS: Studying Metrical Poetry with LLMs"
    assert paper["authors"] == "Chalamalasetti Kranti, Sowmya Vajjala, Jane Doe et al."
    assert paper["venue"] == "Proceedings of the 8th Workshop on Indian Language Data: Resources and Evaluation"
    assert paper["type"] == "proceedings-article"
    assert paper["doi"] == "10.63317/4o3nvxw6qwso"
    assert paper["pages"] == "1--13"
    assert paper["url"] == "https://aclanthology.org/2026.wildre-1.1/"
    assert paper["pdf_url"] == "https://aclanthology.org/2026.wildre-1.1.pdf"
    assert paper["publisher"] == "ELRA Language Resources Association (ELRA)"

    journal = papers[1]
    assert journal["type"] == "journal-article"
    assert journal["venue"] == "Computational Linguistics"
    assert journal["year"] == "2020"


def test_title_search_ranks_the_closer_match_first():
    index = build_index(BIB)
    hits = search_index(index, "metrical poetry with LLMs", limit=5)
    assert hits[0]["id"] == "2026.wildre-1.1"
    assert all(hit["source"] == "ACLAnthology" for hit in hits)


def test_an_anthology_id_resolves_to_that_paper():
    index = build_index(BIB)
    hits = search_index(index, "please open 2020.cl-1.1", limit=5)
    assert hits[0]["id"] == "2020.cl-1.1"
    assert hits[0]["title"] == "A Journal Piece on Parsing"
