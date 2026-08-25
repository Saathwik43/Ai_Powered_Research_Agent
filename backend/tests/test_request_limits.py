"""0.9 — no request path may buffer an unbounded body.

Three layers, and the tests cover each because each has a different bypass:

* the body-size middleware (Content-Length, and a running count for chunked
  requests that declare no length at all),
* `read_upload_capped`, because `await file.read()` then checking `len()`
  commits the memory *before* the check it looks like it performs, and
* the URL-ingest path, which read `resp.text` on whatever the remote sent.
"""

import io
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from core.auth import get_current_user
from core.config import MAX_REQUEST_BODY_BYTES, MAX_UPLOAD_BYTES
from core.file_validation import read_upload_capped

USER_ID = "000000000000000000000001"


async def _fake_current_user():
    return {"user_id": USER_ID, "email": "me@example.com", "role": "user"}


def _upload(data: bytes, filename="big.pdf") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


@pytest.mark.anyio
async def test_read_upload_capped_refuses_an_oversized_file():
    with pytest.raises(HTTPException) as exc:
        await read_upload_capped(_upload(b"x" * (MAX_UPLOAD_BYTES + 1)), MAX_UPLOAD_BYTES)
    assert exc.value.status_code == 413


@pytest.mark.anyio
async def test_read_upload_capped_returns_a_normal_file():
    data = b"%PDF-1.4" + b"x" * 100
    assert await read_upload_capped(_upload(data), MAX_UPLOAD_BYTES) == data


@pytest.mark.anyio
async def test_read_upload_capped_rejects_an_empty_file():
    with pytest.raises(HTTPException) as exc:
        await read_upload_capped(_upload(b""), MAX_UPLOAD_BYTES)
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_read_upload_capped_stops_at_the_boundary_not_after():
    """The point of chunked reading: an over-cap upload must never be fully
    materialised, so the reader stops as soon as the running total crosses."""
    read_sizes = []

    class _CountingUpload:
        def __init__(self, total):
            self.remaining = total

        async def read(self, size):
            chunk = b"x" * min(size, self.remaining)
            self.remaining -= len(chunk)
            read_sizes.append(len(chunk))
            return chunk

    with pytest.raises(HTTPException):
        await read_upload_capped(_CountingUpload(50 * 1024 * 1024), 2 * 1024 * 1024)

    # 2MB cap, 1MB chunks: stops on the third chunk, not after 50MB.
    assert sum(read_sizes) <= 3 * 1024 * 1024


class TestBodySizeMiddleware(unittest.TestCase):
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

    def test_oversized_declared_body_is_413(self):
        oversized = b"x" * (MAX_REQUEST_BODY_BYTES + 1024)
        response = self.client.post(
            "/api/manuscript/save",
            content=oversized,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_oversized_chunked_body_is_413(self):
        """No Content-Length at all — the running byte count is the only guard."""
        def _chunks():
            for _ in range(MAX_REQUEST_BODY_BYTES // (256 * 1024) + 4):
                yield b"x" * (256 * 1024)

        response = self.client.post(
            "/api/manuscript/save",
            content=_chunks(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_a_normal_body_is_untouched(self):
        with patch("routers.manuscript.db", {"manuscripts": _NoopCollection()}):
            response = self.client.post(
                "/api/manuscript/save",
                json={"topic": "small draft", "content": {"abstract": "hi"}},
            )
        self.assertEqual(response.status_code, 200)


class _NoopCollection:
    async def find_one(self, *a, **k):
        return None

    async def insert_one(self, doc):
        from bson import ObjectId

        return type("R", (), {"inserted_id": ObjectId()})()

    async def update_one(self, *a, **k):
        return type("R", (), {"matched_count": 1})()


class TestUrlSourceIngestIsCapped(unittest.TestCase):
    """The URL branch of /api/sources/upload goes through the pinned, capped
    fetch rather than `httpx.get(...).text`."""

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

    def test_oversize_remote_content_surfaces_as_413(self):
        too_big = HTTPException(status_code=413, detail="Remote content is larger than the 5MB limit.")
        with patch("routers.pdf.safe_fetch", AsyncMock(side_effect=too_big)):
            response = self.client.post(
                "/api/sources/upload",
                data={"url": "https://example.com/huge", "topic": "t"},
            )
        self.assertEqual(response.status_code, 413)

    def test_blocked_target_surfaces_as_400_not_a_generic_failure(self):
        from core.ssrf_guard import SsrfError

        with patch("routers.pdf.safe_fetch", AsyncMock(side_effect=SsrfError())):
            response = self.client.post(
                "/api/sources/upload",
                data={"url": "http://169.254.169.254/latest/meta-data/", "topic": "t"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not allowed", response.json()["detail"])
