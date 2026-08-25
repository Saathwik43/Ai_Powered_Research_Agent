"""
1.14 — rotating refresh tokens, and the user lookup that used to run on every
authenticated request.

The property worth pinning is not "refresh works". It is what happens when a
refresh token is presented twice: there is no way to tell the legitimate client
from whoever copied it, so both are signed out. Without that, rotation is just a
second long-lived credential.
"""

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from unittest.mock import patch

from core import auth


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _matches(self, doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        if doc is None and upsert:
            doc = dict(query)
            self.docs.append(doc)
        if doc is None:
            return type("R", (), {"matched_count": 0, "modified_count": 0})()
        doc.update(update.get("$set") or {})
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    async def update_many(self, query, update):
        matched = [d for d in self.docs if self._matches(d, query)]
        for doc in matched:
            doc.update(update.get("$set") or {})
        return type("R", (), {"matched_count": len(matched), "modified_count": len(matched)})()


USER_OID = ObjectId()
USER_ID = str(USER_OID)
EMAIL = "researcher@example.com"


def _db(user_overrides=None, tokens=None):
    user = {
        "_id": USER_OID, "email": EMAIL, "name": "Researcher", "role": "user",
        "email_verified": True, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    user.update(user_overrides or {})
    return {"users": _FakeCollection([user]), "refresh_tokens": _FakeCollection(tokens or [])}


@pytest.fixture(autouse=True)
def _clear_user_cache():
    auth.invalidate_user_cache()
    yield
    auth.invalidate_user_cache()


@pytest.mark.anyio
async def test_a_session_carries_both_tokens():
    db = _db()
    with patch.object(auth, "db", db):
        session = await auth.issue_session(USER_ID, EMAIL)

    assert session["token"] and session["refresh_token"]
    assert session["expires_in"] == int(auth.ACCESS_TOKEN_EXPIRE_HOURS * 3600)
    assert len(db["refresh_tokens"].docs) == 1
    assert db["refresh_tokens"].docs[0]["used"] is False


@pytest.mark.anyio
async def test_refresh_returns_a_new_pair_and_retires_the_old_one():
    db = _db()
    with patch.object(auth, "db", db):
        session = await auth.issue_session(USER_ID, EMAIL)
        rotated = await auth.rotate_refresh_token(session["refresh_token"])

    assert rotated["refresh_token"] != session["refresh_token"]
    assert rotated["user"]["email"] == EMAIL
    spent, live = sorted(db["refresh_tokens"].docs, key=lambda d: bool(d.get("used")), reverse=True)
    assert spent["used"] is True and spent.get("replaced_by")
    assert live["used"] is False


@pytest.mark.anyio
async def test_a_replayed_refresh_token_kills_the_whole_family():
    """Two parties hold the same token and the server cannot tell them apart,
    so neither keeps the session."""
    db = _db()
    with patch.object(auth, "db", db):
        session = await auth.issue_session(USER_ID, EMAIL)
        rotated = await auth.rotate_refresh_token(session["refresh_token"])

        with pytest.raises(HTTPException) as exc:
            await auth.rotate_refresh_token(session["refresh_token"])
        assert exc.value.status_code == 401

        # The token the honest client is holding is dead too.
        with pytest.raises(HTTPException):
            await auth.rotate_refresh_token(rotated["refresh_token"])

    assert all(d["used"] for d in db["refresh_tokens"].docs)


@pytest.mark.anyio
async def test_an_access_token_is_not_a_refresh_token():
    """Independent signing keys, so presenting one as the other fails at the
    signature check rather than at a claim nobody remembered to read."""
    access = auth.create_access_token(USER_ID, EMAIL)
    with pytest.raises(HTTPException) as exc:
        auth.decode_refresh_token(access)
    assert exc.value.status_code == 401

    db = _db()
    with patch.object(auth, "db", db):
        session = await auth.issue_session(USER_ID, EMAIL)
    with pytest.raises(HTTPException):
        auth.decode_access_token(session["refresh_token"])


@pytest.mark.anyio
async def test_a_suspended_account_cannot_refresh():
    db = _db({"status": "suspended"})
    with patch.object(auth, "db", db):
        session = await auth.issue_session(USER_ID, EMAIL)
        with pytest.raises(HTTPException) as exc:
            await auth.rotate_refresh_token(session["refresh_token"])
    assert exc.value.status_code == 403
    assert all(d["used"] for d in db["refresh_tokens"].docs)


@pytest.mark.anyio
async def test_a_deleted_account_cannot_refresh():
    db = _db()
    with patch.object(auth, "db", db):
        session = await auth.issue_session(USER_ID, EMAIL)
        db["users"].docs.clear()
        with pytest.raises(HTTPException) as exc:
            await auth.rotate_refresh_token(session["refresh_token"])
    assert exc.value.status_code == 401


# ─── Cached user lookup ────────────────────────────────────────────────────────

class _CountingUsers(_FakeCollection):
    def __init__(self, docs=None):
        super().__init__(docs)
        self.reads = 0

    async def find_one(self, query, projection=None):
        self.reads += 1
        return await super().find_one(query, projection)


@pytest.mark.anyio
async def test_the_users_document_is_read_once_not_per_request():
    users = _CountingUsers([{"_id": USER_OID, "email": EMAIL, "role": "user", "name": "R"}])
    with patch.object(auth, "db", {"users": users}):
        for _ in range(5):
            assert (await auth._load_user(USER_ID))["email"] == EMAIL
    assert users.reads == 1


@pytest.mark.anyio
async def test_an_admin_change_takes_effect_immediately():
    """The cache is why role and suspension changes need an explicit
    invalidation — otherwise a demotion leaves admin access live for a minute."""
    users = _CountingUsers([{"_id": USER_OID, "email": EMAIL, "role": "admin", "name": "R"}])
    with patch.object(auth, "db", {"users": users}):
        assert (await auth._load_user(USER_ID))["role"] == "admin"
        users.docs[0]["role"] = "user"
        auth.invalidate_user_cache(USER_ID)
        assert (await auth._load_user(USER_ID))["role"] == "user"
    assert users.reads == 2


@pytest.mark.anyio
async def test_a_missing_user_is_not_re_queried_every_request():
    users = _CountingUsers([])
    with patch.object(auth, "db", {"users": users}):
        assert await auth._load_user(USER_ID) is None
        assert await auth._load_user(USER_ID) is None
    assert users.reads == 1
