"""Zenodo publication records mapped onto the shared paper dict."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

from integrations.zenodo import record_to_paper, search_papers

CONFERENCE = {
    "id": 14161987,
    "conceptrecid": "14161986",
    "doi": "10.5281/zenodo.14161987",
    "metadata": {
        "title": "Dependency Graph Parsing as Sequence Labeling",
        "doi": "10.5281/zenodo.14161987",
        "publication_date": "2024-11-14",
        "description": "<p>We frame dependency parsing as <em>sequence labeling</em>.</p>",
        "access_right": "open",
        "creators": [
            {"name": "Ezquerro, Ana"},
            {"name": "Vilares, David"},
            {"name": "Gomez, Carlos"},
            {"name": "Lee, Min"},
        ],
        "keywords": ["Dependency parsing", "sequence labeling"],
        "resource_type": {"type": "publication", "subtype": "conferencepaper"},
        "meeting": {"title": "Empirical Methods in Natural Language Processing", "acronym": "EMNLP 2024"},
        "relations": {"version": [{"is_last": True, "parent": {"pid_value": "14161986"}}]},
    },
    "links": {"self_html": "https://zenodo.org/records/14161987"},
    "files": [{
        "key": "paper.pdf",
        "links": {"self": "https://zenodo.org/api/records/14161987/files/paper.pdf/content"},
    }],
}

DATASET = {
    "id": 99,
    "metadata": {
        "title": "A dataset",
        "resource_type": {"type": "dataset", "subtype": "dataset"},
        "creators": [{"name": "Ada, Lovelace"}],
    },
}

OLD_VERSION = {
    "id": 10,
    "conceptrecid": "14161986",
    "metadata": {
        "title": "Dependency Graph Parsing as Sequence Labeling",
        "publication_date": "2024-01-01",
        "resource_type": {"type": "publication", "subtype": "preprint"},
        "relations": {"version": [{"is_last": False}]},
        "creators": [{"name": "Ezquerro, Ana"}],
    },
}

CLOSED = {
    "id": 11,
    "conceptrecid": "11",
    "doi": "10.5281/zenodo.11",
    "metadata": {
        "title": "Closed preprint",
        "publication_date": "2023",
        "access_right": "closed",
        "description": "An abstract.",
        "resource_type": {"type": "publication", "subtype": "preprint"},
        "creators": [{"name": "Ada Lovelace"}],
    },
    "links": {"self_html": "https://zenodo.org/records/11"},
    "files": [{"key": "paper.pdf", "links": {"self": "https://zenodo.org/files/paper.pdf"}}],
}


def test_conference_record_becomes_a_proceedings_paper():
    paper = record_to_paper(CONFERENCE)
    assert paper["source"] == "Zenodo"
    assert paper["title"] == "Dependency Graph Parsing as Sequence Labeling"
    assert paper["authors"] == "Ana Ezquerro, David Vilares, Carlos Gomez et al."
    assert paper["year"] == "2024"
    assert paper["abstract"] == "We frame dependency parsing as sequence labeling."
    assert paper["venue"] == "Empirical Methods in Natural Language Processing"
    assert paper["type"] == "proceedings-article"
    assert paper["doi"] == "10.5281/zenodo.14161987"
    assert paper["pdf_url"].endswith("/content")
    assert paper["categories"] == ["Dependency parsing", "sequence labeling"]
    assert paper["url"] == "https://zenodo.org/records/14161987"


def test_datasets_and_old_versions_are_dropped():
    assert record_to_paper(DATASET) is None
    assert record_to_paper(OLD_VERSION) is None
    assert record_to_paper("nope") is None


def test_a_closed_preprint_keeps_the_landing_page_and_drops_the_file():
    paper = record_to_paper(CLOSED)
    assert paper["type"] == "posted-content"
    assert paper["subtype"] == "preprint"
    assert paper["pdf_url"] == ""
    assert paper["authors"] == "Ada Lovelace"
    assert paper["year"] == "2023"


def test_search_requests_publications_and_skips_non_papers():
    seen = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"hits": {"hits": [CONFERENCE, DATASET, OLD_VERSION, CLOSED]}}

    class FakeClient:
        async def get(self, url, params=None):
            seen["url"] = url
            seen["params"] = params
            return FakeResp()

    @asynccontextmanager
    async def fake_pool(*args, **kwargs):
        yield FakeClient()

    async def run():
        with patch("integrations.zenodo.pooled_client", fake_pool):
            return await search_papers("dependency parsing", limit=10)

    papers = asyncio.run(run())
    assert seen["url"].endswith("/api/records")
    assert seen["params"]["type"] == "publication"
    assert seen["params"]["q"] == "dependency parsing"
    assert [p["id"] for p in papers] == ["14161987", "11"]
