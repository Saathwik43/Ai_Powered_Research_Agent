"""`usage_tracker.current_user_id` must be readable inside the SSE body.

The response body of `/api/manuscript/stream` is iterated *after* the endpoint
returns, and everything billing- or provenance-related that runs during the
stream reads the caller's id from a ContextVar set back in `get_current_user`:
`usage_tracker.check_quota`, `log_usage`, and `_store_reference_snapshot`. If
that lookup returns None the stream still produces a perfectly good draft, so
nothing visibly fails -- the damage only shows up later as an unbilled
generation and, at export time, "this draft has no stored reference set".

That silence is why this is pinned by a test rather than left to inspection.
It passes today; it exists so a future middleware or task-spawning change that
breaks context propagation is caught here instead of in the export dialog.
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.auth import get_current_user
from core.config import SECURITY_HEADERS
from services import usage_tracker

USER_ID = "000000000000000000000001"

STREAM_PAYLOAD = {
    "topic": "context propagation probe",
    "section": "abstract",
    "context": "",
    "citation_style": "ieee",
    "mode": "manual",
    "provider": "groq",
}


async def _fake_current_user():
    """Stands in for core.auth.get_current_user, including its ContextVar set."""
    usage_tracker.current_user_id.set(USER_ID)
    return {"user_id": USER_ID, "email": "probe@example.com", "role": "user"}


async def _probe_stream(*args, **kwargs):
    """Reports what the generator body can see, in place of a real generation."""
    yield {"type": "probe", "seen": usage_tracker.current_user_id.get()}
    yield {"type": "done"}


class TestStreamUserContext(unittest.TestCase):
    def setUp(self):
        from main import app

        self.app = app
        # Restore rather than pop: tests/test_suggest.py installs its own
        # get_current_user override at import time and never removes it, so
        # popping here silently un-authenticates whatever runs after us.
        self._sentinel = object()
        self._previous = app.dependency_overrides.get(get_current_user, self._sentinel)
        app.dependency_overrides[get_current_user] = _fake_current_user
        self.client = TestClient(app)
        usage_tracker.current_user_id.set(None)

    def tearDown(self):
        if self._previous is self._sentinel:
            self.app.dependency_overrides.pop(get_current_user, None)
        else:
            self.app.dependency_overrides[get_current_user] = self._previous

    def test_current_user_id_visible_inside_sse_body(self):
        with patch("ai.manuscript_generation.generate_section_stream", _probe_stream):
            response = self.client.post("/api/manuscript/stream", json=STREAM_PAYLOAD)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'"seen": "{USER_ID}"', response.text)
        self.assertNotIn('"seen": null', response.text)

    def test_security_headers_present_on_stream(self):
        with patch("ai.manuscript_generation.generate_section_stream", _probe_stream):
            headers = self.client.post("/api/manuscript/stream", json=STREAM_PAYLOAD).headers

        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(headers.get(name), value, f"missing header {name}")

    def test_security_headers_present_on_plain_route(self):
        headers = self.client.get("/").headers
        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(headers.get(name), value, f"missing header {name}")


if __name__ == "__main__":
    unittest.main()
