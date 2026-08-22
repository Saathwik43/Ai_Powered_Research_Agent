import asyncio

from services.api_telemetry import _EVENTS, _LOCK, recent_events, track_call_sync
from services import admin_status as ast


def test_recent_events_newest_first_and_filters():
    with _LOCK:
        _EVENTS.clear()
    with track_call_sync("Springer", "search") as rec:
        rec.succeed(http_status=200, items=3)
    with track_call_sync("DOAJ", "search") as rec:
        rec.fail(http_status=403, error="forbidden")

    events = recent_events(10)
    assert [e["name"] for e in events[:2]] == ["DOAJ", "Springer Nature"]
    assert events[0]["ok"] is False
    assert events[0]["operation"] == "search"

    fails = recent_events(10, ok=False)
    assert len(fails) == 1
    assert fails[0]["name"] == "DOAJ"

    core = recent_events(10, name="Springer Nature")
    assert len(core) == 1
    assert core[0]["ok"] is True


def test_disabled_sources_map_to_search_tasks():
    ast._DISABLED.clear()
    ast._DISABLED_LOADED = True
    ast._DISABLED.add("Springer Nature")
    ast._DISABLED.add("DOAJ")
    ast._DISABLED.add("Unpaywall")
    tasks = ast.get_disabled_search_tasks()
    assert tasks == {"Springer", "DOAJ", "Unpaywall"}
    assert ast.is_enabled("Springer Nature") is False
    assert ast.is_enabled("OpenAlex") is True
    ast._DISABLED.clear()


def test_arxiv_atom_xml_probe_is_operational():
    import asyncio
    from unittest.mock import MagicMock, patch

    from services import admin_status as ast

    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>An electron paper</title></entry>
    </feed>"""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/atom+xml"}
    resp.text = xml
    resp.json.side_effect = ValueError("not json")

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            return resp

    with patch("services.admin_status.httpx.AsyncClient", _Client):
        result = asyncio.run(ast.check_arxiv())
    assert result["status"] == "operational"
    assert result["probe"]["items"] == 1


def test_probe_one_patches_cache_without_running_other_checks():
    async def fake_source():
        return {
            "name": "DOAJ",
            "category": "Literature Sources",
            "status": "operational",
            "details": "Search OK",
        }

    original = ast.CHECK_BY_NAME["DOAJ"]
    ast.CHECK_BY_NAME["DOAJ"] = fake_source
    prev_payload = ast._STATUS_CACHE.get("payload")
    prev_ts = ast._STATUS_CACHE.get("ts")
    ast._STATUS_CACHE["payload"] = {
        "sources": [
            {"name": "DOAJ", "status": "offline", "details": "old"},
            {"name": "Groq", "status": "operational", "details": "cached-groq"},
        ]
    }
    try:
        result = asyncio.run(ast.probe_one("DOAJ"))
        assert result["name"] == "DOAJ"
        assert result["status"] == "operational"
        cached = ast._STATUS_CACHE["payload"]["sources"]
        assert cached[0]["status"] == "operational"
        assert cached[1] == {"name": "Groq", "status": "operational", "details": "cached-groq"}
    finally:
        ast.CHECK_BY_NAME["DOAJ"] = original
        ast._STATUS_CACHE["payload"] = prev_payload
        ast._STATUS_CACHE["ts"] = prev_ts
