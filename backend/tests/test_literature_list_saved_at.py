"""Saved Surveys list must expose a date for every row.

Pre-`saved_at` documents only have the ObjectId clock. Listing them without a
fill made the Literature Survey Saved tab alternate between
"N papers screened" and "N papers screened · date".
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId
from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}
app.state.limiter.enabled = False
client = TestClient(app)


def _cursor(docs):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


class TestLiteratureListSavedAt(unittest.TestCase):
    def test_list_backfills_saved_at_from_object_id(self):
        oid = ObjectId()
        doc = {
            "_id": oid,
            "query": "perovskite efficiency",
            "papers": [{"title": "a"}],
            "screened": 30,
        }
        collection = MagicMock()
        collection.find.return_value = _cursor([doc])

        with patch("routers.discovery.db", {"literature": collection}):
            resp = client.get("/api/literature/list")

        self.assertEqual(resp.status_code, 200)
        survey = resp.json()["data"][0]
        self.assertNotIn("_id", survey)
        self.assertTrue(survey.get("saved_at"))
        parsed = datetime.fromisoformat(survey["saved_at"])
        self.assertEqual(parsed, oid.generation_time.replace(tzinfo=timezone.utc))

    def test_list_keeps_existing_saved_at(self):
        oid = ObjectId()
        existing = "2026-09-16T12:00:00+00:00"
        doc = {
            "_id": oid,
            "query": "machine learning",
            "papers": [],
            "screened": 15,
            "saved_at": existing,
        }
        collection = MagicMock()
        collection.find.return_value = _cursor([doc])

        with patch("routers.discovery.db", {"literature": collection}):
            resp = client.get("/api/literature/list")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"][0]["saved_at"], existing)
