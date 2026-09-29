"""OpenReview notes/search mapped onto the shared paper dict."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

from integrations.openreview import note_to_paper, search_papers


CONFERENCE = {
    "id": "ECTxVRFhUa",
    "pdate": 1758216830915,
    "invitations": ["NeurIPS.cc/2025/Conference/-/Submission"],
    "content": {
        "title": {"value": "Tensor Product Attention Is All You Need"},
        "authors": {"value": ["Yifan Zhang", "Yifeng Liu", "Huizhuo Yuan", "Zhen Qin"]},
        "abstract": {"value": "Scaling language models."},
        "venue": {"value": "NeurIPS 2025 spotlight"},
        "venueid": {"value": "NeurIPS.cc/2025/Conference"},
        "pdf": {"value": "/pdf/b1bc3409d0cb3a3bb5220c7615cd88a9c13f7a4e.pdf"},
        "keywords": {"value": ["Attention mechanisms", "Transformer"]},
    },
}

DBLP_CORR = {
    "id": "o2pnEi65bX",
    "cdate": 1735689600000,
    "invitations": ["DBLP.org/-/Record"],
    "content": {
        "title": {"value": "Tensor Product Attention Is All You Need"},
        "authors": {"value": ["Yifan Zhang"]},
        "abstract": {"value": "Scaling language models."},
        "venue": {"value": "CoRR 2025"},
        "venueid": {"value": "dblp.org/journals/CORR/2025"},
        "pdf": {"value": "http://arxiv.org/pdf/2501.06425v3"},
        "html": {"value": "https://doi.org/10.48550/arXiv.2501.06425"},
    },
}

REVIEW = {
    "id": "rev1",
    "invitations": ["ICLR.cc/2025/Conference/Submission5495/-/Official_Review"],
    "content": {"review": {"value": "This paper is interesting."}},
}


def test_conference_submission_becomes_a_proceedings_paper():
    paper = note_to_paper(CONFERENCE)
    assert paper["source"] == "OpenReview"
    assert paper["title"] == "Tensor Product Attention Is All You Need"
    assert paper["authors"] == "Yifan Zhang, Yifeng Liu, Huizhuo Yuan et al."
    assert paper["year"] == "2025"
    assert paper["venue"] == "NeurIPS 2025 spotlight"
    assert paper["type"] == "proceedings-article"
    assert paper["url"] == "https://openreview.net/forum?id=ECTxVRFhUa"
    assert paper["pdf_url"] == "https://openreview.net/pdf/b1bc3409d0cb3a3bb5220c7615cd88a9c13f7a4e.pdf"
    assert paper["categories"] == ["Attention mechanisms", "Transformer"]
    assert "subtype" not in paper


def test_dblp_corr_record_is_a_preprint_with_a_doi():
    paper = note_to_paper(DBLP_CORR)
    assert paper["doi"] == "10.48550/arXiv.2501.06425"
    assert paper["eprint"] == "2501.06425"
    assert paper["type"] == "posted-content"
    assert paper["subtype"] == "preprint"
    assert paper["pdf_url"] == "http://arxiv.org/pdf/2501.06425v3"
    assert paper["url"] == "https://doi.org/10.48550/arXiv.2501.06425"
    assert paper["year"] == "2025"


def test_a_review_without_a_title_is_not_a_paper():
    assert note_to_paper(REVIEW) is None
    assert note_to_paper({}) is None
    assert note_to_paper("nope") is None


def test_search_asks_for_forum_notes_and_drops_reviews():
    seen = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"notes": [CONFERENCE, REVIEW, DBLP_CORR], "count": 3}

    class FakeClient:
        async def get(self, url, params=None):
            seen["url"] = url
            seen["params"] = params
            return FakeResp()

    @asynccontextmanager
    async def fake_pool(*args, **kwargs):
        yield FakeClient()

    async def run():
        with patch("integrations.openreview.pooled_client", fake_pool):
            return await search_papers("attention", limit=20)

    papers = asyncio.run(run())
    assert seen["url"].endswith("/notes/search")
    assert seen["params"]["source"] == "forum"
    assert seen["params"]["term"] == "attention"
    assert seen["params"]["limit"] == 20
    assert len(papers) == 2
    assert all(p["source"] == "OpenReview" for p in papers)


def test_search_failure_returns_an_empty_list():
    class FakeClient:
        async def get(self, url, params=None):
            raise RuntimeError("upstream down")

    @asynccontextmanager
    async def fake_pool(*args, **kwargs):
        yield FakeClient()

    async def run():
        with patch("integrations.openreview.pooled_client", fake_pool):
            return await search_papers("attention")

    assert asyncio.run(run()) == []


def test_blank_query_does_not_call_the_api():
    called = MagicMock()

    @asynccontextmanager
    async def fake_pool(*args, **kwargs):
        called()
        yield None

    async def run():
        with patch("integrations.openreview.pooled_client", fake_pool):
            return await search_papers("   ")

    assert asyncio.run(run()) == []
    called.assert_not_called()
