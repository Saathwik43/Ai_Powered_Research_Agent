"""0.3 — PDF-chat cache keys must be built from a verified owner.

`chat_id` arrives in the request body. Two server-side caches were keyed on it
directly: the rolling conversation summary (`_rolling_summaries`) and the Gemini
context cache (`pdf:<chat_id>`). A chat id is a Mongo ObjectId — enumerable in
bulk from a timestamp — so sending somebody else's id returned analysis built
over *their* uploaded paper, without ever touching a document the caller owned.
"""

import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi.testclient import TestClient

from core.auth import get_current_user

MY_ID = "000000000000000000000001"
OTHER_CHAT = str(ObjectId())


async def _fake_current_user():
    return {"user_id": MY_ID, "email": "me@example.com", "role": "user"}


class _Chats:
    """Only the caller's own chat exists as far as this collection is concerned."""

    def __init__(self, owned_ids, records=None):
        self.owned = set(owned_ids)
        self.records = records or {}
        self.queries = []

    async def find_one(self, query, projection=None):
        self.queries.append(query)
        if query.get("user_id") != MY_ID:
            return None
        if str(query.get("_id")) in self.owned:
            extra = self.records.get(str(query.get("_id")), {})
            return {"_id": query["_id"], "user_id": MY_ID, **extra}
        return None


class TestPdfChatScope(unittest.TestCase):
    def setUp(self):
        from main import app

        self.app = app
        self._sentinel = object()
        self._previous = app.dependency_overrides.get(get_current_user, self._sentinel)
        app.dependency_overrides[get_current_user] = _fake_current_user
        self.client = TestClient(app)

    def tearDown(self):
        # Restore rather than pop -- test_suggest.py installs an override at
        # import time, and popping un-authenticates whatever runs after us (T1).
        if self._previous is self._sentinel:
            self.app.dependency_overrides.pop(get_current_user, None)
        else:
            self.app.dependency_overrides[get_current_user] = self._previous

    def test_someone_elses_chat_id_is_a_404(self):
        chats = _Chats(owned_ids=set())
        analyze = AsyncMock(return_value={"type": "custom", "content": "leak"})
        with patch("routers.pdf.db", {"pdf_chats": chats}), \
             patch("routers.pdf.analyze_uploaded_paper", analyze):
            response = self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"text": "a" * 300, "custom_prompt": "what is this?", "chat_id": OTHER_CHAT},
            )
        self.assertEqual(response.status_code, 404)
        analyze.assert_not_awaited()

    def test_owned_chat_id_is_namespaced_by_user(self):
        mine = str(ObjectId())
        chats = _Chats(owned_ids={mine})
        analyze = AsyncMock(return_value={"type": "custom", "content": "ok"})
        with patch("routers.pdf.db", {"pdf_chats": chats}), \
             patch("routers.pdf.analyze_uploaded_paper", analyze):
            response = self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"text": "a" * 300, "custom_prompt": "what is this?", "chat_id": mine},
            )
        self.assertEqual(response.status_code, 200)
        cache_scope = analyze.await_args.args[4]
        # The user id is part of the key, so two users cannot collide even if a
        # chat id were somehow reused.
        self.assertEqual(cache_scope, f"{MY_ID}:{mine}")

    def test_ownership_lookup_is_scoped_to_the_caller(self):
        mine = str(ObjectId())
        chats = _Chats(owned_ids={mine})
        with patch("routers.pdf.db", {"pdf_chats": chats}), \
             patch("routers.pdf.analyze_uploaded_paper", AsyncMock(return_value={})):
            self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"text": "a" * 300, "custom_prompt": "q", "chat_id": mine},
            )
        self.assertEqual(chats.queries[0]["user_id"], MY_ID)

    def test_no_chat_id_means_no_cache_scope(self):
        analyze = AsyncMock(return_value={"type": "custom", "content": "ok"})
        with patch("routers.pdf.db", {"pdf_chats": _Chats(set())}), \
             patch("routers.pdf.analyze_uploaded_paper", analyze):
            response = self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"text": "a" * 300, "custom_prompt": "q"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(analyze.await_args.args[4])

    def test_malformed_chat_id_is_a_400_not_a_500(self):
        with patch("routers.pdf.db", {"pdf_chats": _Chats(set())}), \
             patch("routers.pdf.analyze_uploaded_paper", AsyncMock(return_value={})):
            response = self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"text": "a" * 300, "custom_prompt": "q", "chat_id": "not-an-oid"},
            )
        self.assertEqual(response.status_code, 400)

    def test_follow_up_hydrates_text_structure_history_from_saved_chat(self):
        """B4 — a follow-up turn may send only chat_id; the paper lives on pdf_chats."""
        mine = str(ObjectId())
        paper = "P" * 300
        structure = {"title": "T", "authors": ["A"], "abstract": "Abs", "sections": {"intro": "Hello"}}
        messages = [
            {"role": "assistant", "type": "text", "content": "Paper is ready."},
            {"role": "user", "type": "text", "content": "What is the method?"},
            {"role": "assistant", "type": "text", "content": "They use attention."},
            {"role": "assistant", "type": "text", "isLoading": True, "content": ""},
        ]
        chats = _Chats(
            owned_ids={mine},
            records={mine: {"text": paper, "structure": structure, "messages": messages}},
        )
        analyze = AsyncMock(return_value={"type": "custom", "content": "ok"})
        with patch("routers.pdf.db", {"pdf_chats": chats}), \
             patch("routers.pdf.analyze_uploaded_paper", analyze):
            response = self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"custom_prompt": "summarise", "chat_id": mine},
            )
        self.assertEqual(response.status_code, 200)
        args = analyze.await_args.args
        self.assertEqual(args[0], paper)
        self.assertEqual(args[1], "summarise")
        self.assertEqual(args[2], structure)
        self.assertEqual(
            args[3],
            [
                {"role": "assistant", "content": "Paper is ready."},
                {"role": "user", "content": "What is the method?"},
                {"role": "assistant", "content": "They use attention."},
            ],
        )
        self.assertEqual(args[4], f"{MY_ID}:{mine}")

    def test_no_chat_id_and_no_text_is_400(self):
        analyze = AsyncMock(return_value={"type": "custom", "content": "ok"})
        with patch("routers.pdf.db", {"pdf_chats": _Chats(set())}), \
             patch("routers.pdf.analyze_uploaded_paper", analyze):
            response = self.client.post(
                "/api/manuscript/analyze-pdf",
                data={"custom_prompt": "q"},
            )
        self.assertEqual(response.status_code, 400)
        analyze.assert_not_awaited()
