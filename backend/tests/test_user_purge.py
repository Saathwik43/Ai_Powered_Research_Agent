"""0.13 — deleting a user must delete what they stored.

Three collections and the GridFS bucket were missing from the delete path, so
"delete this account" left the user's uploaded source documents (`sources`),
their saved literature surveys (`literature`), their reference snapshots and the
actual PDF bytes sitting in the database under an id that no longer resolved to
anybody. `sources.raw_text` is the full text of whatever they uploaded, so this
was the largest residue of the three.
"""

import unittest
from unittest.mock import AsyncMock, patch

import pytest

from routers.admin import _USER_OWNED_COLLECTIONS, purge_user_data

USER_ID = "000000000000000000000009"


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.deleted_queries = []

    async def delete_many(self, query):
        self.deleted_queries.append(query)
        before = len(self.docs)
        self.docs = [d for d in self.docs if not all(d.get(k) == v for k, v in query.items())]
        return type("R", (), {"deleted_count": before - len(self.docs)})()

    def find(self, query, projection=None):
        matched = [d for d in self.docs if _matches(d, query)]
        return _Cursor(matched)


def _matches(doc, query):
    for key, value in query.items():
        if "." in key:
            head, tail = key.split(".", 1)
            if (doc.get(head) or {}).get(tail) != value:
                return False
        elif doc.get(key) != value:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield d

        return gen()


@pytest.mark.anyio
async def test_purge_clears_every_user_owned_collection():
    db = {
        name: _Collection([{"user_id": USER_ID}, {"user_id": "someone-else"}])
        for name in _USER_OWNED_COLLECTIONS
    }
    db["pdfs.files"] = _Collection()

    with patch("routers.admin.db", db), \
         patch("core.database.get_pdf_bucket", lambda: AsyncMock()):
        deleted = await purge_user_data(USER_ID)

    for name in _USER_OWNED_COLLECTIONS:
        assert deleted[name] == 1, name
        # Another user's document in the same collection is untouched.
        assert db[name].docs == [{"user_id": "someone-else"}], name


@pytest.mark.anyio
async def test_sources_and_literature_are_included():
    """These two are named explicitly because they are the ones the old delete
    path forgot, and `sources` holds uploaded document text."""
    assert "sources" in _USER_OWNED_COLLECTIONS
    assert "literature" in _USER_OWNED_COLLECTIONS
    assert "manuscript_references" in _USER_OWNED_COLLECTIONS


@pytest.mark.anyio
async def test_gridfs_pdfs_are_deleted_through_the_bucket():
    """Through the bucket, not by dropping `pdfs.files`: deleting the file
    document alone orphans the chunks, which are all of the bytes."""
    db = {name: _Collection() for name in _USER_OWNED_COLLECTIONS}
    db["pdfs.files"] = _Collection([
        {"_id": "file-1", "metadata": {"user_id": USER_ID}},
        {"_id": "file-2", "metadata": {"user_id": "someone-else"}},
    ])
    bucket = AsyncMock()

    with patch("routers.admin.db", db), patch("core.database.get_pdf_bucket", lambda: bucket):
        deleted = await purge_user_data(USER_ID)

    assert deleted["pdfs"] == 1
    bucket.delete.assert_awaited_once_with("file-1")


@pytest.mark.anyio
async def test_one_failing_collection_does_not_abort_the_rest():
    class _Broken(_Collection):
        async def delete_many(self, query):
            raise RuntimeError("mongo said no")

    db = {name: _Collection([{"user_id": USER_ID}]) for name in _USER_OWNED_COLLECTIONS}
    db["usage_logs"] = _Broken()
    db["pdfs.files"] = _Collection()

    with patch("routers.admin.db", db), patch("core.database.get_pdf_bucket", lambda: AsyncMock()):
        deleted = await purge_user_data(USER_ID)

    assert deleted["usage_logs"] == -1
    assert deleted["sources"] == 1


class TestDeleteRoute(unittest.TestCase):
    def test_malformed_user_id_is_400_not_500(self):
        from fastapi.testclient import TestClient

        from core.auth import get_current_user
        from main import app

        async def _admin():
            return {"user_id": "000000000000000000000001", "email": "a@b.c", "role": "admin"}

        sentinel = object()
        previous = app.dependency_overrides.get(get_current_user, sentinel)
        app.dependency_overrides[get_current_user] = _admin
        try:
            response = TestClient(app).delete("/api/admin/users/not-an-objectid")
        finally:
            if previous is sentinel:
                app.dependency_overrides.pop(get_current_user, None)
            else:
                app.dependency_overrides[get_current_user] = previous
        self.assertEqual(response.status_code, 400)
