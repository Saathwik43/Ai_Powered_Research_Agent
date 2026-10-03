"""Pin and rename for the saved lists — PDF chats, literature surveys and
manuscript drafts (ROW-1).

One copy of the rules so the three collections cannot drift:

* ``pinned`` is stored only as ``True``. Unpinning *removes* the field rather
  than writing ``False``: Mongo sorts a missing field below ``False``, so a mix
  of ``False`` and missing would split the unpinned rows into two runs and the
  list would stop being newest-first.
* ``title`` is a display name only. It never replaces the key a row is found
  by (a survey's ``query``, a draft's ``topic``, a chat's ``filename``), so a
  rename cannot orphan the references, versions or file stored under that key.
  An empty title removes the field and the row shows its original name again.
* Lists sort pinned first, then newest (``_id`` descending). A page cursor
  names which run it stopped in — ``p:<id>`` or ``u:<id>`` — because an
  ``_id`` alone cannot say whether the pinned run is finished. A bare id, as
  issued before ROW-1, is read as a position in the unpinned run.
"""

from typing import Optional

from bson import ObjectId
from fastapi import HTTPException

TITLE_MAX = 200
LIST_SORT = [("pinned", -1), ("_id", -1)]


def meta_update(pinned: Optional[bool], title: Optional[str]) -> dict:
    """The Mongo update for a pin / rename request. 400 when it changes nothing."""
    set_fields: dict = {}
    unset_fields: dict = {}
    if pinned is True:
        set_fields["pinned"] = True
    elif pinned is False:
        unset_fields["pinned"] = ""
    if title is not None:
        clean = " ".join(title.split())[:TITLE_MAX]
        if clean:
            set_fields["title"] = clean
        else:
            unset_fields["title"] = ""
    if not set_fields and not unset_fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    update: dict = {}
    if set_fields:
        update["$set"] = set_fields
    if unset_fields:
        update["$unset"] = unset_fields
    return update


def cursor_filter(cursor: Optional[str]) -> dict:
    """The extra list filter that resumes after ``cursor`` in ``LIST_SORT`` order."""
    if not cursor:
        return {}
    tier, _, raw = cursor.rpartition(":")
    try:
        oid = ObjectId(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor.")
    if tier == "p":
        return {"$or": [
            {"pinned": True, "_id": {"$lt": oid}},
            {"pinned": {"$ne": True}},
        ]}
    if tier in ("", "u"):
        return {"pinned": {"$ne": True}, "_id": {"$lt": oid}}
    raise HTTPException(status_code=400, detail="Invalid cursor.")


def next_cursor(docs: list, limit: int) -> Optional[str]:
    """Cursor for the page after ``docs`` (raw Mongo docs, ``_id`` still set)."""
    if not docs or len(docs) < limit:
        return None
    last = docs[-1]
    tier = "p" if last.get("pinned") is True else "u"
    return f"{tier}:{last['_id']}"


def parse_object_id(raw: str, what: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except Exception:
        raise HTTPException(status_code=404, detail=f"{what} not found.")


def meta_view(doc: dict) -> dict:
    """What a pin / rename route answers with: the row's state after the write."""
    return {"pinned": doc.get("pinned") is True, "title": doc.get("title")}
