import asyncio

from services.api_telemetry import _EVENTS, _LOCK, recent_events, track_call_sync
from services import admin_status as ast


def test_recent_events_newest_first_and_filters():
    with _LOCK:
        _EVENTS.clear()
    with track_call_sync("CORE", "search") as rec:
        rec.succeed(http_status=200, items=3)
    with track_call_sync("BASE", "search") as rec:
        rec.fail(http_status=403, error="forbidden")

    events = recent_events(10)
    assert [e["name"] for e in events[:2]] == ["BASE", "CORE"]
    assert events[0]["ok"] is False
    assert events[0]["operation"] == "search"

    fails = recent_events(10, ok=False)
    assert len(fails) == 1
    assert fails[0]["name"] == "BASE"

    core = recent_events(10, name="CORE")
    assert len(core) == 1
    assert core[0]["ok"] is True


def test_disabled_sources_map_to_search_tasks():
    ast._DISABLED.clear()
    ast._DISABLED_LOADED = True
    ast._DISABLED.add("CORE")
    ast._DISABLED.add("BASE")
    ast._DISABLED.add("Unpaywall")
    tasks = ast.get_disabled_search_tasks()
    assert tasks == {"CORE", "BASE", "Unpaywall"}
    assert ast.is_enabled("CORE") is False
    assert ast.is_enabled("OpenAlex") is True
    ast._DISABLED.clear()


def test_probe_one_patches_cache_without_running_other_checks():
    async def fake_core():
        return {
            "name": "CORE",
            "category": "Literature Sources",
            "status": "operational",
            "details": "Search OK",
        }

    original = ast.CHECK_BY_NAME["CORE"]
    ast.CHECK_BY_NAME["CORE"] = fake_core
    prev_payload = ast._STATUS_CACHE.get("payload")
    prev_ts = ast._STATUS_CACHE.get("ts")
    ast._STATUS_CACHE["payload"] = {
        "sources": [
            {"name": "CORE", "status": "offline", "details": "old"},
            {"name": "Groq", "status": "operational", "details": "cached-groq"},
        ]
    }
    try:
        result = asyncio.run(ast.probe_one("CORE"))
        assert result["name"] == "CORE"
        assert result["status"] == "operational"
        cached = ast._STATUS_CACHE["payload"]["sources"]
        assert cached[0]["status"] == "operational"
        assert cached[1] == {"name": "Groq", "status": "operational", "details": "cached-groq"}
    finally:
        ast.CHECK_BY_NAME["CORE"] = original
        ast._STATUS_CACHE["payload"] = prev_payload
        ast._STATUS_CACHE["ts"] = prev_ts
