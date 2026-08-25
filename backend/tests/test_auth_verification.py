"""0.4 — an address must be proved before the account works, and a Google
sign-in must not adopt a password nobody verified.

The takeover this closes, step by step:

  1. Attacker signs up as victim@example.com with a password they choose.
     Nothing checks the address, so the account is live immediately.
  2. Victim, who has never used the site, clicks "Sign in with Google".
  3. The old code found the existing document by email and issued a session on
     it — the victim's account, with the attacker's password still attached.
  4. Everything the victim writes from then on is readable by the attacker,
     who just logs in with the password they set in step 1.

Two changes break the chain: step 1 no longer produces a usable account, and
step 3 discards the unverified password instead of linking to it.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from core import auth


class _FakeUsers:
    """Enough of a Motor collection for the auth functions under test."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def update_one(self, query, update):
        doc = await self.find_one(query)
        if not doc:
            return type("R", (), {"matched_count": 0, "modified_count": 0})()
        for key, value in (update.get("$set") or {}).items():
            doc[key] = value
        for key in (update.get("$unset") or {}):
            doc.pop(key, None)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = int(doc.get(key, 0)) + value
        return type("R", (), {"matched_count": 1, "modified_count": 1})()


class _FakeRefreshTokens(_FakeUsers):
    """Refresh-token rotation writes here on every successful sign-in (1.14)."""

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
        matched = [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]
        for doc in matched:
            doc.update(update.get("$set") or {})
        return type("R", (), {"matched_count": len(matched), "modified_count": len(matched)})()


def _fake_db(users, refresh=None):
    return {"users": users, "refresh_tokens": refresh if refresh is not None else _FakeRefreshTokens()}


VICTIM = "victim@example.com"


def _password_account(**overrides):
    doc = {
        "_id": ObjectId(),
        "email": VICTIM,
        "name": "Victim",
        "password": auth.hash_password("attacker7pass"),
        "email_verified": False,
        "verify_token_version": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    doc.update(overrides)
    return doc


# ─── Signup no longer hands out a session ──────────────────────────────────────

@pytest.mark.anyio
async def test_signup_returns_no_token_and_marks_unverified():
    users = _FakeUsers()
    with patch.object(auth, "db", _fake_db(users)), \
         patch.object(auth, "_send_verification", AsyncMock(return_value=True)):
        result = await auth.signup_user(VICTIM, "correct7horse", "Victim")

    assert "token" not in result
    assert result["requires_verification"] is True
    assert users.docs[0]["email_verified"] is False


@pytest.mark.anyio
async def test_signup_rejects_a_weak_password():
    users = _FakeUsers()
    with patch.object(auth, "db", _fake_db(users)):
        with pytest.raises(HTTPException) as exc:
            await auth.signup_user(VICTIM, "password", "Victim")
    assert exc.value.status_code == 400
    assert users.docs == []


@pytest.mark.anyio
async def test_login_is_refused_until_the_address_is_verified():
    users = _FakeUsers([_password_account()])
    with patch.object(auth, "db", _fake_db(users)):
        with pytest.raises(HTTPException) as exc:
            await auth.login_user(VICTIM, "attacker7pass")
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_login_works_once_verified():
    users = _FakeUsers([_password_account(email_verified=True)])
    with patch.object(auth, "db", _fake_db(users)):
        result = await auth.login_user(VICTIM, "attacker7pass")
    assert result["token"]


@pytest.mark.anyio
async def test_wrong_password_reports_credentials_not_verification_state():
    """The 403 must sit behind the password check, or it becomes an oracle for
    which addresses have a pending signup."""
    users = _FakeUsers([_password_account()])
    with patch.object(auth, "db", _fake_db(users)):
        with pytest.raises(HTTPException) as exc:
            await auth.login_user(VICTIM, "not7theirpassword")
    assert exc.value.status_code == 401


# ─── Verification link ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_verification_activates_the_account_and_signs_in():
    account = _password_account()
    users = _FakeUsers([account])
    token = auth.create_verification_token(str(account["_id"]), VICTIM, 0)

    with patch.object(auth, "db", _fake_db(users)):
        result = await auth.verify_email_with_token(token)
        assert result["token"]
        assert account["email_verified"] is True
        # Single use: the version bump invalidates the link just consumed.
        with pytest.raises(HTTPException):
            await auth.verify_email_with_token(token)


# ─── The Google-link takeover ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_google_signin_discards_an_unverified_password():
    account = _password_account()
    users = _FakeUsers([account])

    with patch.object(auth, "db", _fake_db(users)):
        result = await auth.google_auth_user(VICTIM, "Victim", None)

    assert result["token"]
    # The attacker's credential is gone, so they cannot log back in beside the
    # victim, and any reset link they were holding is dead.
    assert "password" not in account
    assert account["email_verified"] is True
    assert account.get("reset_token_version") == 1
    assert account["auth_provider"] == "google"


@pytest.mark.anyio
async def test_google_signin_keeps_a_verified_password_account_intact():
    """A user who verified their address and then adds Google must keep being
    able to sign in with their password."""
    hashed = auth.hash_password("their7own")
    account = _password_account(password=hashed, email_verified=True)
    users = _FakeUsers([account])

    with patch.object(auth, "db", _fake_db(users)):
        await auth.google_auth_user(VICTIM, "Victim", None)

    assert account["password"] == hashed
    assert account.get("google_linked") is True
    # Not relabelled as a Google-only account, or the password login path would
    # start telling them to "sign in with Google" instead.
    assert account.get("auth_provider") != "google"


@pytest.mark.anyio
async def test_new_google_account_is_created_verified():
    users = _FakeUsers()
    with patch.object(auth, "db", _fake_db(users)):
        await auth.google_auth_user("fresh@example.com", "Fresh", None)
    assert users.docs[0]["email_verified"] is True
    assert "password" not in users.docs[0]


# ─── Reset also proves the mailbox ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_completing_a_reset_verifies_the_address():
    account = _password_account(reset_token_version=0)
    users = _FakeUsers([account])
    token = auth.create_reset_token(str(account["_id"]), VICTIM, 0)

    with patch.object(auth, "db", _fake_db(users)):
        await auth.reset_password_with_token(token, "brandnew7pass")

    assert account["email_verified"] is True
    assert account["reset_token_version"] == 1


@pytest.mark.anyio
async def test_reset_enforces_the_password_rules():
    account = _password_account()
    users = _FakeUsers([account])
    token = auth.create_reset_token(str(account["_id"]), VICTIM, 0)
    with patch.object(auth, "db", _fake_db(users)):
        with pytest.raises(HTTPException) as exc:
            await auth.reset_password_with_token(token, "password")
    assert exc.value.status_code == 400


class TestGoogleRouteRequiresVerifiedClaim(unittest.TestCase):
    """Google can assert an address it never confirmed (Workspace domains may
    skip verification). Trusting that would reopen the takeover above."""

    def test_unverified_google_email_is_refused(self):
        from fastapi.testclient import TestClient

        from main import app

        client = TestClient(app)
        with patch(
            "routers.auth.verify_google_token",
            return_value={"email": VICTIM, "name": "V", "email_verified": False},
        ):
            response = client.post("/api/auth/google", json={"token": "x" * 20})
        self.assertEqual(response.status_code, 403)

    def test_missing_email_verified_claim_is_refused(self):
        from fastapi.testclient import TestClient

        from main import app

        client = TestClient(app)
        with patch(
            "routers.auth.verify_google_token",
            return_value={"email": VICTIM, "name": "V"},
        ):
            response = client.post("/api/auth/google", json={"token": "x" * 20})
        self.assertEqual(response.status_code, 403)
