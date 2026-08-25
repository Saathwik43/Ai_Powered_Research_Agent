"""Pins the contained P1 batch: PyJWT, literature diary fields, edit stream, pagination defaults."""

import inspect

from fastapi.params import Query

from ai.manuscript_generation import edit_section_stream
from core import auth
from routers.discovery import list_literature_surveys
from routers.manuscript import list_manuscript_drafts
from routers.pdf import list_pdf_chats
from schemas import LiteratureSavePayload

USER_ID = "000000000000000000000001"
EMAIL = "probe@example.com"


def test_pyjwt_roundtrip_keeps_purpose_aud_iss():
    token = auth.create_access_token(USER_ID, EMAIL)
    payload = auth.decode_access_token(token)
    assert payload["sub"] == USER_ID
    assert payload["purpose"] == auth.PURPOSE_ACCESS
    assert payload["aud"] == auth.AUDIENCE_ACCESS
    assert payload["iss"] == auth.JWT_ISSUER


def test_literature_save_payload_keeps_diary_fields():
    payload = LiteratureSavePayload(
        query="graph neural networks",
        papers=[{"title": "A"}],
        screened=12,
        sources=[{"name": "OpenAlex", "status": "ok", "count": 5}],
    )
    assert payload.screened == 12
    assert payload.sources[0]["name"] == "OpenAlex"


def test_edit_section_stream_is_async_generator():
    assert inspect.isasyncgenfunction(edit_section_stream)


def test_auto_mode_does_not_require_a_named_provider():
    from fastapi import HTTPException
    from ai.model_allowlist import bound_requested_model

    assert bound_requested_model("auto", None, None) == (None, None)
    assert bound_requested_model("manual", None, None) == (None, None)
    assert bound_requested_model("manual", "groq", None) == ("groq", None)
    try:
        bound_requested_model("manual", "not-a-provider", "secret-model")
        raise AssertionError("expected 400")
    except HTTPException as exc:
        assert exc.status_code == 400


def _query_default(fn, name):
    param = inspect.signature(fn).parameters[name]
    default = param.default
    assert isinstance(default, Query)
    return default.default


def test_list_endpoints_default_to_cursor_page_of_50():
    for fn in (list_manuscript_drafts, list_literature_surveys, list_pdf_chats):
        assert _query_default(fn, "limit") == 50
        assert "cursor" in inspect.signature(fn).parameters
