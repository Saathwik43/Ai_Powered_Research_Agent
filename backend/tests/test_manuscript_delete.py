"""
Draft load/delete identity: empty papers, whitespace topics, and draft ids.

Load and delete used to require a trimmed topic string, so a created-but-unwritten
paper (empty content, blank/whitespace topic, or only an id) could neither open
nor be removed.
"""

from unittest.mock import patch

from bson import ObjectId
from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
client = TestClient(app)

OID_MINE = ObjectId("507f1f77bcf86cd799439011")
OID_OTHER = ObjectId("507f1f77bcf86cd799439012")
OID_EMPTY = ObjectId("507f1f77bcf86cd799439013")
OID_SPACES = ObjectId("507f1f77bcf86cd799439014")


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.queries = []

    async def find_one(self, query, *args, **kwargs):
        self.queries.append(("find_one", query))
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def delete_one(self, query):
        self.queries.append(("delete_one", query))
        for i, doc in enumerate(self.docs):
            if all(doc.get(k) == v for k, v in query.items()):
                self.docs.pop(i)
                return _DeleteResult(1)
        return _DeleteResult(0)


def _fake_db(manuscripts, references):
    return {
        "manuscripts": manuscripts,
        "manuscript_references": references,
    }


def test_delete_draft_removes_manuscript_and_reference_snapshot():
    topic = "transformer attention mechanisms"
    manuscripts = _FakeCollection([
        {"_id": OID_MINE, "user_id": "test_user", "topic": topic, "content": {"abstract": "A"}},
        {"_id": OID_OTHER, "user_id": "other_user", "topic": topic, "content": {"abstract": "B"}},
    ])
    references = _FakeCollection([
        {"user_id": "test_user", "topic": topic, "references": [{"index": "1"}]},
        {"user_id": "other_user", "topic": topic, "references": [{"index": "9"}]},
    ])

    with patch("routers.manuscript.db", _fake_db(manuscripts, references)):
        response = client.delete("/api/manuscript/delete", params={"topic": topic})

    assert response.status_code == 200
    body = response.json()
    assert body["topic"] == topic
    assert body["message"] == "Draft deleted."
    assert [d["user_id"] for d in manuscripts.docs] == ["other_user"]
    assert [d["user_id"] for d in references.docs] == ["other_user"]


def test_delete_empty_content_draft():
    topic = "a paper with no sections yet"
    manuscripts = _FakeCollection([
        {"_id": OID_EMPTY, "user_id": "test_user", "topic": topic, "content": {}},
    ])
    references = _FakeCollection([])

    with patch("routers.manuscript.db", _fake_db(manuscripts, references)):
        response = client.delete("/api/manuscript/delete", params={"topic": topic})

    assert response.status_code == 200
    assert manuscripts.docs == []


def test_delete_draft_by_id():
    topic = "untitled"
    manuscripts = _FakeCollection([
        {"_id": OID_EMPTY, "user_id": "test_user", "topic": topic, "content": {}},
    ])
    references = _FakeCollection([
        {"user_id": "test_user", "topic": topic, "references": []},
    ])

    with patch("routers.manuscript.db", _fake_db(manuscripts, references)):
        response = client.delete(
            "/api/manuscript/delete",
            params={"draft_id": str(OID_EMPTY)},
        )

    assert response.status_code == 200
    assert manuscripts.docs == []
    assert references.docs == []


def test_delete_whitespace_topic_draft():
    stored = "  spaced topic  "
    manuscripts = _FakeCollection([
        {"_id": OID_SPACES, "user_id": "test_user", "topic": stored, "content": {}},
    ])

    with patch("routers.manuscript.db", _fake_db(manuscripts, _FakeCollection())):
        response = client.delete("/api/manuscript/delete", params={"topic": stored})

    assert response.status_code == 200
    assert manuscripts.docs == []


def test_delete_draft_returns_404_when_missing():
    manuscripts = _FakeCollection([])
    references = _FakeCollection([
        {"user_id": "test_user", "topic": "missing paper", "references": []},
    ])

    with patch("routers.manuscript.db", _fake_db(manuscripts, references)):
        response = client.delete(
            "/api/manuscript/delete",
            params={"topic": "missing paper"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No draft found for this topic."
    assert len(references.docs) == 1
    assert not any(kind == "delete_one" for kind, _ in references.queries)


def test_delete_draft_requires_topic_or_id():
    with patch("routers.manuscript.db", _fake_db(_FakeCollection(), _FakeCollection())):
        response = client.delete("/api/manuscript/delete")

    assert response.status_code == 400
    assert response.json()["detail"] == "Topic or draft id is required."


def test_load_empty_content_draft():
    topic = "just a title"
    manuscripts = _FakeCollection([
        {"_id": OID_EMPTY, "user_id": "test_user", "topic": topic, "content": {}},
    ])

    with patch("routers.manuscript.db", _fake_db(manuscripts, _FakeCollection())):
        response = client.get("/api/manuscript/load", params={"topic": topic})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["topic"] == topic
    assert data["content"] == {}
    assert data["id"] == str(OID_EMPTY)
