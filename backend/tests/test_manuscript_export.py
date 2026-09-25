"""
Audit L10 -- the /api/manuscript/export-latex endpoint, at the seam the unit
tests cannot reach: which reference source it reads, and what the zip README
claims about the artefact it just built.

The reported failure was a real export shipping a zero-byte references.bib with
a README promising "cited with \\cite{}" and a citation map that were not in the
zip. The zip is the thing the user opens, so the assertions are on the zip.
"""

import io
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app

# Set inside each test rather than at import time: a module-scope override leaks
# into other files and is the cause of the test_manuscript_delete ordering bug.
USER = {"user_id": "export_user"}

TOPIC = "perovskite efficiency"

SNAPSHOT_REFS = [
    {"index": "1", "title": "Electron injection", "authors": "J. Liu", "year": "2024",
     "doi": "10.1000/aaa"},
    {"index": "2", "title": "Perovskite LEDs", "authors": "W. Bai", "year": "2023"},
]


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query, *args, **kwargs):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(self, query, update, upsert=False):
        for i, doc in enumerate(self.docs):
            if all(doc.get(k) == v for k, v in query.items()):
                self.docs[i] = {**doc, **(update.get("$set") or {})}
                return
        if upsert:
            self.docs.append({**(query or {}), **(update.get("$set") or {})})


def _db(manuscript_doc, snapshot_doc=None, literature_doc=None):
    return {
        "manuscripts": _FakeCollection([manuscript_doc]),
        "manuscript_references": _FakeCollection([snapshot_doc] if snapshot_doc else []),
        "literature": _FakeCollection([literature_doc] if literature_doc else []),
    }


def _draft(content, manuscript_refs=None):
    return {
        "user_id": USER["user_id"],
        "topic": TOPIC,
        "content": content,
        "manuscript_refs": manuscript_refs or {},
    }


client = TestClient(app)


def _export(db_map, expect=200):
    # Restore whatever was there rather than popping: several other test modules
    # install this override at import time and never reinstall it, so popping it
    # here breaks them by collection order (the test_gap_analysis /
    # test_manuscript_delete failure, from the other side).
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        with patch("routers.manuscript.db", db_map):
            response = client.post(
                "/api/manuscript/export-latex",
                json={"topic": TOPIC, "venue": "ieee"},
            )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous
    assert response.status_code == expect, response.text
    return response


def _zip_members(response):
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


class TestExportWithASnapshot:
    """The path Phase 2 was built for: manuscript_references carries the [N] map."""

    def _members(self):
        db_map = _db(
            _draft({"abstract": "Reached 22.2% [1].", "results": "And [2]."}),
            {"user_id": USER["user_id"], "topic": TOPIC, "references": SNAPSHOT_REFS},
        )
        return _zip_members(_export(db_map))

    def test_the_bib_is_not_empty(self):
        members = self._members()
        assert members["references.bib"].count("@article{") == 2

    def test_the_tex_cites_and_carries_a_citation_map(self):
        members = self._members()
        assert "\\cite{" in members["paper.tex"]
        assert "% --- Citation map" in members["paper.tex"]

    def test_the_readme_claims_match_the_zip(self):
        members = self._members()
        readme = members["README.txt"]
        assert "2 entries" in readme
        assert "\\cite{}" in readme
        assert "citation map is included" in readme
        # Every file the README names must actually be in the zip. The L10
        # failure was a README describing an artefact the export did not ship.
        for named in ("paper.tex", "references.bib", "references.ris", "references.csl.json"):
            assert named in readme
            assert named in members

    def test_the_reference_manager_files_describe_the_same_set(self):
        """2.8 — .bib, .ris and .csl.json are three views of one list.

        They are built from a shared `resolve_export_references` call precisely
        so they cannot drift; this pins that they agree on the record count.
        """
        import json

        members = self._members()
        assert members["references.ris"].count("TY  - ") == 2
        assert members["references.ris"].count("ER  - ") == 2
        assert len(json.loads(members["references.csl.json"])) == 2

    def test_the_bib_never_names_the_search_service_as_a_venue(self):
        """2.8 — the shipped regression: `journal = {OpenAlex}`."""
        members = self._members()
        for service in ("OpenAlex", "Semantic Scholar", "Crossref", "PubMed"):
            assert service not in members["references.bib"]


class TestExportWithNoReferenceSet:
    """
    L10: a draft that predates snapshot persistence has no manuscript_references
    document, and manuscript_refs holds display strings the exporter cannot use.
    That used to export a zip with an empty bibliography; it must now refuse.
    """

    def test_citing_an_index_absent_from_the_display_list_is_a_400(self):
        db_map = _db(_draft(
            {"abstract": "Reached 22.2% [1].", "results": "And [5]."},
            manuscript_refs={"1": 'J. Liu, "Electron injection", 2024.'},
        ))
        response = _export(db_map, expect=400)
        detail = response.json()["detail"]
        assert "[5]" in detail
        assert "[1]" not in detail
        assert "Regenerate" in detail

    def test_display_list_lets_a_cited_draft_export_without_a_snapshot(self):
        db_map = _db(_draft(
            {"abstract": "Reached 22.2% [1]."},
            manuscript_refs={"1": 'J. Liu, "Electron injection", 2024.'},
        ))
        members = _zip_members(_export(db_map))
        assert members["references.bib"].count("@article{") == 1
        assert "\\cite{" in members["paper.tex"]

    def test_an_uncited_draft_still_exports(self):
        # No markers, nothing to break -- an early draft must not be blocked.
        db_map = _db(_draft({"abstract": "No citations yet."}))
        members = _zip_members(_export(db_map))
        assert members["references.bib"] == ""

    def test_the_readme_does_not_promise_cites_that_are_not_there(self):
        db_map = _db(_draft({"abstract": "No citations yet."}))
        readme = _zip_members(_export(db_map))["README.txt"]
        assert "citation map is included" not in readme
        assert "no \\cite commands were emitted" in readme


class TestExportRebuildsFromLiterature:
    """L14: display-only manuscript_refs + a saved survey rebuild the snapshot."""

    def test_matching_survey_papers_export_without_regenerating(self):
        db_map = _db(
            _draft(
                {"abstract": "Reached 22.2% [1]."},
                manuscript_refs={"1": 'J. Liu, "Electron injection", 2024.'},
            ),
            literature_doc={
                "user_id": USER["user_id"],
                "query": TOPIC,
                "papers": [{"title": "Electron injection", "authors": "J. Liu",
                            "year": "2024", "doi": "10.1000/aaa"}],
            },
        )
        members = _zip_members(_export(db_map))
        assert members["references.bib"].count("@article{") == 1
        assert "\\cite{" in members["paper.tex"]
        assert db_map["manuscript_references"].docs


class TestExportRepairsHoleySnapshot:
    """A stored snapshot can drop indices the prose still cites (partial L14)."""

    def test_display_strings_fill_missing_snapshot_indices(self):
        holey = [
            {"index": str(i), "title": f"Source {i}", "authors": "A. Author", "year": "2024"}
            for i in (1, 3, 4, 5, 9, 10, 11, 12, 13, 14, 15)
        ]
        db_map = _db(
            _draft(
                {"results": "See [2], [6], [7] and [8]."},
                manuscript_refs={
                    "2": 'Z. Two, "Second paper", 2023.',
                    "6": 'Z. Six, "Sixth paper", 2023.',
                    "7": 'Z. Seven, "Seventh paper", 2023.',
                    "8": 'Z. Eight, "Eighth paper", 2023.',
                },
            ),
            {"user_id": USER["user_id"], "topic": TOPIC, "references": holey},
        )
        members = _zip_members(_export(db_map))
        assert "\\cite{" in members["paper.tex"]
        assert members["references.bib"].count("@article{") == 15


class TestLegacyReferenceFallback:
    """manuscript_refs is still honoured when it happens to hold paper dicts."""

    def test_nested_dicts_are_used_when_no_snapshot_exists(self):
        db_map = _db(_draft(
            {"abstract": "Reached 22.2% [1]."},
            manuscript_refs={"1": {"title": "Electron injection", "authors": "J. Liu",
                                   "year": "2024"}},
        ))
        members = _zip_members(_export(db_map))
        assert members["references.bib"].count("@article{") == 1
        assert "\\cite{" in members["paper.tex"]
