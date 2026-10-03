"""PDF-4 — the same paper uploaded twice is stored once and opens one chat.

Before this, every upload stored the bytes again and the client saved a fresh
chat, so a user who re-uploaded a paper got a second 7MB copy and a duplicate
history entry. These tests pin the three outcomes of an upload: new bytes are
extracted and stored and remembered; known bytes with a chat hand that chat
back without extracting or storing; known bytes whose chat was deleted reuse the
stored file but extract again, because the text lived on the deleted chat.
"""

import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi.testclient import TestClient

from core import object_store
from core.auth import get_current_user
from core.object_store import content_digest, find_stored_pdf, remember_stored_pdf

MY_ID = "000000000000000000000001"
PDF_BYTES = b"%PDF-1.4\n% dedupe test\n%%EOF\n"
STORED_ID = "5f2b1c8e9d4a3b2c1e0f7a61"


async def _fake_current_user():
    return {"user_id": MY_ID, "email": "me@example.com", "role": "user"}


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.find_calls = []
        self.upserts = []

    async def find_one(self, query, projection=None, sort=None):
        self.find_calls.append(query)
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(self, query, update, upsert=False):
        self.upserts.append((query, update, upsert))


class TestDigestRows(unittest.IsolatedAsyncioTestCase):
    async def test_rows_are_keyed_by_user_and_content(self):
        files = _Collection()
        with patch("core.database.db", {"pdf_files": files}):
            await remember_stored_pdf(MY_ID, "abc", STORED_ID)

        query, update, upsert = files.upserts[0]
        self.assertEqual(query, {"_id": f"{MY_ID}:abc"})
        self.assertEqual(update["$set"]["file_id"], STORED_ID)
        self.assertTrue(upsert)

    async def test_a_row_whose_file_is_gone_is_not_reused(self):
        files = _Collection([{"_id": f"{MY_ID}:abc", "file_id": STORED_ID}])
        with patch("core.database.db", {"pdf_files": files}), \
             patch.object(object_store, "pdf_exists", AsyncMock(return_value=False)):
            self.assertIsNone(await find_stored_pdf(MY_ID, "abc"))

    async def test_another_users_row_is_never_found(self):
        files = _Collection([{"_id": f"{MY_ID}:abc", "file_id": STORED_ID}])
        with patch("core.database.db", {"pdf_files": files}), \
             patch.object(object_store, "pdf_exists", AsyncMock(return_value=True)):
            self.assertIsNone(await find_stored_pdf("00000000000000000000000f", "abc"))
            self.assertEqual(await find_stored_pdf(MY_ID, "abc"), STORED_ID)

    async def test_a_lookup_failure_never_fails_the_upload(self):
        class _Broken:
            async def find_one(self, *a, **k):
                raise RuntimeError("mongo down")

        with patch("core.database.db", {"pdf_files": _Broken()}):
            self.assertIsNone(await find_stored_pdf(MY_ID, "abc"))


class TestExtractRoute(unittest.TestCase):
    def setUp(self):
        from main import app

        self.app = app
        self._sentinel = object()
        self._previous = app.dependency_overrides.get(get_current_user, self._sentinel)
        app.dependency_overrides[get_current_user] = _fake_current_user
        self.client = TestClient(app)

    def tearDown(self):
        if self._previous is self._sentinel:
            self.app.dependency_overrides.pop(get_current_user, None)
        else:
            self.app.dependency_overrides[get_current_user] = self._previous

    def _post(self):
        return self.client.post(
            "/api/manuscript/extract-pdf",
            files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")},
        )

    def _patches(self, *, known_file, chats):
        return (
            patch("routers.pdf.find_stored_pdf", AsyncMock(return_value=known_file)),
            patch("routers.pdf.remember_stored_pdf", AsyncMock()),
            patch("routers.pdf.store_pdf", AsyncMock(return_value="new-file-id")),
            patch("routers.pdf.extract_pdf_text", AsyncMock(return_value="fresh text")),
            patch("routers.pdf.extract_pdf_structure", AsyncMock(return_value={"sections": {}})),
            patch("routers.pdf.user_allows_third_party", AsyncMock(return_value=False)),
            patch("routers.pdf.db", {"pdf_chats": chats}),
        )

    def test_new_bytes_are_extracted_stored_and_remembered(self):
        p = self._patches(known_file=None, chats=_Collection())
        with p[0] as find, p[1] as remember, p[2] as store, p[3] as text, p[4], p[5], p[6]:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["file_id"], "new-file-id")
        self.assertNotIn("existing_chat_id", body)
        find.assert_awaited_once_with(MY_ID, content_digest(PDF_BYTES))
        store.assert_awaited_once()
        remember.assert_awaited_once_with(MY_ID, content_digest(PDF_BYTES), "new-file-id")
        text.assert_awaited_once()

    def test_known_bytes_with_a_chat_return_that_chat_without_extracting(self):
        chat_id = ObjectId()
        chats = _Collection([{
            "_id": chat_id, "user_id": MY_ID, "file_id": STORED_ID,
            "text": "saved text", "structure": {"sections": {"intro": "x"}},
        }])
        p = self._patches(known_file=STORED_ID, chats=chats)
        with p[0], p[1] as remember, p[2] as store, p[3] as text, p[4] as structure, p[5], p[6]:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["existing_chat_id"], str(chat_id))
        self.assertEqual(body["file_id"], STORED_ID)
        self.assertEqual(body["text"], "saved text")
        # The chat lookup is scoped to the caller, not just the file id.
        self.assertEqual(chats.find_calls[0], {"user_id": MY_ID, "file_id": STORED_ID})
        store.assert_not_awaited()
        remember.assert_not_awaited()
        text.assert_not_awaited()
        structure.assert_not_awaited()

    def test_known_bytes_whose_chat_was_deleted_reuse_the_file_but_extract(self):
        p = self._patches(known_file=STORED_ID, chats=_Collection())
        with p[0], p[1] as remember, p[2] as store, p[3] as text, p[4], p[5], p[6]:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["file_id"], STORED_ID)
        self.assertEqual(body["text"], "fresh text")
        self.assertNotIn("existing_chat_id", body)
        store.assert_not_awaited()
        remember.assert_not_awaited()
        text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
