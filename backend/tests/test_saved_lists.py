"""Pin and rename on the saved lists (ROW-1).

These pin the caller's intent: which Mongo update and filter the routes send.
Whether Mongo actually orders and pages those rows as intended was checked
against the real database separately (see implemented.md, ROW-1).
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.auth import get_current_user
from core.saved_lists import LIST_SORT, cursor_filter, meta_update, next_cursor
from main import app

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
app.state.limiter.enabled = False
client = TestClient(app)


def _find_cursor(docs):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


class TestMetaUpdate(unittest.TestCase):
    def test_pin_sets_true(self):
        self.assertEqual(meta_update(True, None), {"$set": {"pinned": True}})

    def test_unpin_removes_the_field_instead_of_writing_false(self):
        # A stored False would sort apart from rows that never had the field.
        self.assertEqual(meta_update(False, None), {"$unset": {"pinned": ""}})

    def test_title_is_whitespace_normalised_and_capped(self):
        update = meta_update(None, "  Survey   of\n perovskites  ")
        self.assertEqual(update, {"$set": {"title": "Survey of perovskites"}})
        long = meta_update(None, "x" * 500)
        self.assertEqual(len(long["$set"]["title"]), 200)

    def test_blank_title_restores_the_original_name(self):
        self.assertEqual(meta_update(None, "   "), {"$unset": {"title": ""}})

    def test_pin_and_rename_together(self):
        self.assertEqual(
            meta_update(False, "New name"),
            {"$set": {"title": "New name"}, "$unset": {"pinned": ""}},
        )

    def test_nothing_to_change_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            meta_update(None, None)
        self.assertEqual(ctx.exception.status_code, 400)


class TestCursor(unittest.TestCase):
    def test_no_cursor_no_filter(self):
        self.assertEqual(cursor_filter(None), {})

    def test_pinned_cursor_finishes_the_pinned_run_then_all_unpinned(self):
        oid = ObjectId()
        self.assertEqual(cursor_filter(f"p:{oid}"), {"$or": [
            {"pinned": True, "_id": {"$lt": oid}},
            {"pinned": {"$ne": True}},
        ]})

    def test_unpinned_and_legacy_bare_cursor_stay_in_the_unpinned_run(self):
        oid = ObjectId()
        expected = {"pinned": {"$ne": True}, "_id": {"$lt": oid}}
        self.assertEqual(cursor_filter(f"u:{oid}"), expected)
        self.assertEqual(cursor_filter(str(oid)), expected)

    def test_bad_cursor_is_400(self):
        for bad in ("nope", "x:" + str(ObjectId()), "p:123"):
            with self.assertRaises(HTTPException) as ctx:
                cursor_filter(bad)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_next_cursor_names_the_run_of_the_last_row(self):
        a, b = ObjectId(), ObjectId()
        self.assertEqual(next_cursor([{"_id": a, "pinned": True}], 1), f"p:{a}")
        self.assertEqual(next_cursor([{"_id": a, "pinned": True}, {"_id": b}], 2), f"u:{b}")
        self.assertIsNone(next_cursor([{"_id": a}], 2))
        self.assertIsNone(next_cursor([], 2))


class TestRoutes(unittest.TestCase):
    """Each PATCH scopes to the caller and answers with the stored state."""

    CASES = [
        ("routers.pdf.db", "pdf_chats", "/api/pdf-chats/{}"),
        ("routers.discovery.db", "literature", "/api/literature/surveys/{}"),
        ("routers.manuscript.db", "manuscripts", "/api/manuscript/drafts/{}"),
    ]

    def test_patch_scopes_to_user_and_returns_state(self):
        for target, name, path in self.CASES:
            with self.subTest(name=name):
                oid = ObjectId()
                collection = MagicMock()
                collection.find_one_and_update = AsyncMock(
                    return_value={"_id": oid, "pinned": True, "title": "Renamed"}
                )
                with patch(target, {name: collection}):
                    resp = client.patch(path.format(oid), json={"pinned": True, "title": " Renamed "})
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertTrue(body["pinned"])
                self.assertEqual(body["title"], "Renamed")
                filt, update = collection.find_one_and_update.call_args.args[:2]
                self.assertEqual(filt, {"_id": oid, "user_id": "test_user"})
                self.assertEqual(update, {"$set": {"pinned": True, "title": "Renamed"}})

    def test_patch_someone_elses_or_missing_row_is_404(self):
        for target, name, path in self.CASES:
            with self.subTest(name=name):
                collection = MagicMock()
                collection.find_one_and_update = AsyncMock(return_value=None)
                with patch(target, {name: collection}):
                    resp = client.patch(path.format(ObjectId()), json={"pinned": True})
                self.assertEqual(resp.status_code, 404)

    def test_patch_bad_id_is_404_and_empty_body_is_400(self):
        for target, name, path in self.CASES:
            with self.subTest(name=name):
                collection = MagicMock()
                collection.find_one_and_update = AsyncMock(return_value=None)
                with patch(target, {name: collection}):
                    self.assertEqual(client.patch(path.format("not-an-id"), json={"pinned": True}).status_code, 404)
                    self.assertEqual(client.patch(path.format(ObjectId()), json={}).status_code, 400)

    def test_lists_sort_pinned_first_and_page_by_run(self):
        lists = [
            ("routers.pdf.db", "pdf_chats", "/api/pdf-chats/list"),
            ("routers.discovery.db", "literature", "/api/literature/list"),
            ("routers.manuscript.db", "manuscripts", "/api/manuscript/list"),
        ]
        for target, name, path in lists:
            with self.subTest(name=name):
                pinned_id, plain_id = ObjectId(), ObjectId()
                docs = [
                    {"_id": pinned_id, "pinned": True, "title": "Mine"},
                    {"_id": plain_id},
                ]
                collection = MagicMock()
                found = _find_cursor(docs)
                collection.find.return_value = found
                with patch(target, {name: collection}):
                    resp = client.get(path, params={"limit": 2, "cursor": f"p:{ObjectId()}"})
                self.assertEqual(resp.status_code, 200)
                found.sort.assert_called_once_with(LIST_SORT)
                query = collection.find.call_args.args[0]
                self.assertEqual(query["user_id"], "test_user")
                self.assertIn("$or", query)
                body = resp.json()
                self.assertEqual(body["next_cursor"], f"u:{plain_id}")
                self.assertTrue(body["data"][0]["pinned"])
                self.assertEqual(body["data"][0]["title"], "Mine")

    def test_survey_list_exposes_the_id_pin_and_rename_address(self):
        oid = ObjectId()
        collection = MagicMock()
        collection.find.return_value = _find_cursor([{"_id": oid, "query": "q", "papers": []}])
        with patch("routers.discovery.db", {"literature": collection}):
            resp = client.get("/api/literature/list")
        self.assertEqual(resp.json()["data"][0]["id"], str(oid))


if __name__ == "__main__":
    unittest.main()
