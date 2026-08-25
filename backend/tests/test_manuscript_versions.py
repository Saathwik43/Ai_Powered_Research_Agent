"""
1.10 — undo a bad save.

Autosave overwrites the draft in place, so an accepted diff the user did not
mean to accept, or a Continue that went off the rails, used to be
unrecoverable. The assertions here are about the two things that make a history
useful rather than decorative: that the snapshot holds the state being
*replaced* (not the one replacing it), and that a restore is itself undoable.
"""

from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app
from routers import manuscript as manuscript_router

USER = "versions_user"
DRAFT_ID = ObjectId("507f1f77bcf86cd799439021")
OTHER_DRAFT_ID = ObjectId("507f1f77bcf86cd799439022")
TOPIC = "graph neural networks for drug discovery"


class _Result:
    def __init__(self, count=1, inserted_id=None):
        self.deleted_count = count
        self.modified_count = count
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return [dict(d) for d in self._docs[:length]]


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self._counter = 0

    def _matches(self, doc, query):
        for field, expected in query.items():
            value = doc.get(field)
            if isinstance(expected, dict) and "$lt" in expected:
                if not (value is not None and value < expected["$lt"]):
                    return False
            elif value != expected:
                return False
        return True

    def _project(self, doc, projection):
        if not projection:
            return dict(doc)
        if any(v == 0 for v in projection.values()):
            return {k: v for k, v in doc.items() if projection.get(k, 1) != 0}
        keep = set(projection) | {"_id"}
        return {k: v for k, v in doc.items() if k in keep}

    async def find_one(self, query, projection=None, sort=None):
        found = [d for d in self.docs if self._matches(d, query)]
        if sort:
            key, direction = sort[0]
            found.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self._project(found[0], projection) if found else None

    def find(self, query, projection=None):
        return _Cursor([self._project(d, projection)
                        for d in self.docs if self._matches(d, query)])

    async def insert_one(self, doc):
        self._counter += 1
        stored = dict(doc)
        stored.setdefault("_id", ObjectId(f"{self._counter:024x}"))
        self.docs.append(stored)
        return _Result(inserted_id=stored["_id"])

    async def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, filt):
                doc.update(update.get("$set") or {})
                return _Result(1)
        return _Result(0)

    async def delete_one(self, filt):
        for i, doc in enumerate(self.docs):
            if self._matches(doc, filt):
                self.docs.pop(i)
                return _Result(1)
        return _Result(0)

    async def delete_many(self, filt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._matches(d, filt)]
        return _Result(before - len(self.docs))


class _FakeDb(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _FakeCollection()
        return dict.__getitem__(self, name)


@pytest.fixture
def client():
    # Restore, never pop: test_suggest.py installs its own override at import
    # time, so popping this one would leave every later module unauthenticated.
    # That is the leak class T1 was opened for.
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER}
    yield TestClient(app)
    if previous is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = previous


@pytest.fixture
def db():
    fake = _FakeDb()
    fake["manuscripts"] = _FakeCollection([{
        "_id": DRAFT_ID, "user_id": USER, "topic": TOPIC,
        "content": {"abstract": "the original abstract"},
        "citation_style": "ieee", "updated_at": "2026-08-20T10:00:00+00:00",
    }])
    fake["manuscript_versions"] = _FakeCollection()
    fake["manuscript_references"] = _FakeCollection()
    with patch.object(manuscript_router, "db", fake):
        yield fake


def _save(client, content, topic=TOPIC):
    return client.post("/api/manuscript/save", json={"topic": topic, "content": content})


def test_overwriting_a_draft_stores_what_it_replaced(client, db):
    response = _save(client, {"abstract": "a worse abstract"})
    assert response.status_code == 200

    versions = db["manuscript_versions"].docs
    assert len(versions) == 1
    # The snapshot is the state being replaced, not the one replacing it.
    assert versions[0]["content"] == {"abstract": "the original abstract"}
    assert versions[0]["manuscript_id"] == str(DRAFT_ID)
    assert versions[0]["reason"] == "save"
    assert db["manuscripts"].docs[0]["content"] == {"abstract": "a worse abstract"}


def test_a_new_draft_has_no_history(client, db):
    response = _save(client, {"abstract": "first words"}, topic="a brand new topic")
    assert response.status_code == 200
    assert db["manuscript_versions"].docs == []


def test_identical_saves_do_not_fill_the_history(client, db):
    """Autosave fires on a debounce. An unchanged body is not a version — it
    would push a real one out of the bounded list."""
    _save(client, {"abstract": "revision one"})   # snapshots the original
    _save(client, {"abstract": "revision one"})   # snapshots "revision one"
    after_two = len(db["manuscript_versions"].docs)
    assert after_two == 2

    for _ in range(5):
        _save(client, {"abstract": "revision one"})
    assert len(db["manuscript_versions"].docs) == after_two


def test_versions_list_omits_the_section_text(client, db):
    _save(client, {"abstract": "shorter"})
    response = client.get("/api/manuscript/versions", params={"topic": TOPIC})
    assert response.status_code == 200
    body = response.json()
    entry = body["data"][0]
    assert "content" not in entry
    assert entry["summary"]["sections"] == ["abstract"]
    assert entry["summary"]["total_chars"] == len("the original abstract")
    assert body["draft_id"] == str(DRAFT_ID)


def test_restore_puts_the_old_text_back_and_stays_undoable(client, db):
    _save(client, {"abstract": "the regrettable rewrite"})
    version_id = db["manuscript_versions"].docs[0]["_id"]

    response = client.post("/api/manuscript/restore", json={"version_id": str(version_id)})
    assert response.status_code == 200
    assert response.json()["data"]["content"] == {"abstract": "the original abstract"}
    assert db["manuscripts"].docs[0]["content"] == {"abstract": "the original abstract"}

    # The restore itself is an overwrite, so what it replaced is recoverable.
    reasons = [v["reason"] for v in db["manuscript_versions"].docs]
    assert "restore" in reasons
    restored_away = next(v for v in db["manuscript_versions"].docs if v["reason"] == "restore")
    assert restored_away["content"] == {"abstract": "the regrettable rewrite"}


def test_version_belonging_to_another_user_is_not_readable(client, db):
    db["manuscript_versions"].docs.append({
        "_id": ObjectId("507f1f77bcf86cd799439099"),
        "user_id": "somebody_else",
        "manuscript_id": str(OTHER_DRAFT_ID),
        "content": {"abstract": "private text"},
    })
    response = client.get("/api/manuscript/version/507f1f77bcf86cd799439099")
    assert response.status_code == 404
    restore = client.post(
        "/api/manuscript/restore", json={"version_id": "507f1f77bcf86cd799439099"}
    )
    assert restore.status_code == 404


def test_history_is_bounded(client, db):
    for i in range(manuscript_router.MAX_VERSIONS_PER_DRAFT + 6):
        _save(client, {"abstract": f"revision {i}"})
    assert len(db["manuscript_versions"].docs) <= manuscript_router.MAX_VERSIONS_PER_DRAFT
    # The newest snapshot is still the one just replaced.
    newest = max(db["manuscript_versions"].docs, key=lambda d: d["_id"])
    last = manuscript_router.MAX_VERSIONS_PER_DRAFT + 4
    assert newest["content"] == {"abstract": f"revision {last}"}


def test_deleting_a_draft_removes_its_history(client, db):
    _save(client, {"abstract": "something"})
    assert db["manuscript_versions"].docs
    response = client.request("DELETE", "/api/manuscript/delete", params={"topic": TOPIC})
    assert response.status_code == 200
    assert db["manuscript_versions"].docs == []


def test_a_failed_snapshot_does_not_fail_the_save(client, db):
    """Losing a version is bad. Losing the save the user just made is worse."""
    class _Broken(_FakeCollection):
        async def find_one(self, *a, **k):
            raise RuntimeError("versions collection unavailable")

    db["manuscript_versions"] = _Broken()
    response = _save(client, {"abstract": "must still land"})
    assert response.status_code == 200
    assert db["manuscripts"].docs[0]["content"] == {"abstract": "must still land"}
