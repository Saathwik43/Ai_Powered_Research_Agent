"""0.2 — a password-reset token must not be usable as a session token.

The original hole was not subtle: reset and access tokens were signed with the
same key, and `decode_access_token` never looked at the `purpose` claim. Pasting
the token out of a reset email into `Authorization: Bearer` was a full login,
from a credential that sits in plaintext in an inbox and in the URL bar.

These pin both barriers independently, so removing either one fails a test:
distinct signing keys, and an explicit purpose/audience/issuer check.
"""

import pytest
from fastapi import HTTPException
import jwt

from core import auth

USER_ID = "000000000000000000000001"
EMAIL = "probe@example.com"


def test_reset_token_is_rejected_as_an_access_token():
    reset = auth.create_reset_token(USER_ID, EMAIL, version=0)
    with pytest.raises(HTTPException) as exc:
        auth.decode_access_token(reset)
    assert exc.value.status_code == 401


def test_verification_token_is_rejected_as_an_access_token():
    verify = auth.create_verification_token(USER_ID, EMAIL, version=0)
    with pytest.raises(HTTPException) as exc:
        auth.decode_access_token(verify)
    assert exc.value.status_code == 401


def test_access_token_is_rejected_as_a_reset_token():
    """The other direction matters too: a stolen session token must not be
    redeemable for a password change."""
    access = auth.create_access_token(USER_ID, EMAIL)
    with pytest.raises(HTTPException) as exc:
        auth.decode_reset_token(access)
    assert exc.value.status_code == 400


def test_reset_and_access_tokens_use_different_keys():
    reset = auth.create_reset_token(USER_ID, EMAIL, version=0)
    # Signature check alone rejects it, before any claim is consulted.
    with pytest.raises(Exception):
        jwt.decode(reset, auth.SECRET_KEY, algorithms=[auth.ALGORITHM],
                   audience=auth.AUDIENCE_RESET, issuer=auth.JWT_ISSUER)
    assert auth.RESET_SECRET_KEY != auth.SECRET_KEY
    assert auth.VERIFY_SECRET_KEY not in (auth.SECRET_KEY, auth.RESET_SECRET_KEY)


def test_access_token_carries_audience_issuer_and_purpose():
    claims = jwt.decode(
        auth.create_access_token(USER_ID, EMAIL),
        auth.SECRET_KEY,
        algorithms=[auth.ALGORITHM],
        audience=auth.AUDIENCE_ACCESS,
        issuer=auth.JWT_ISSUER,
    )
    assert claims["aud"] == auth.AUDIENCE_ACCESS
    assert claims["iss"] == auth.JWT_ISSUER
    assert claims["purpose"] == auth.PURPOSE_ACCESS
    assert claims["sub"] == USER_ID


def test_token_for_another_audience_is_rejected():
    """A token minted by some other service that happens to share the secret
    still fails, because the audience does not name this API."""
    from datetime import datetime, timedelta, timezone

    foreign = jwt.encode(
        {
            "sub": USER_ID,
            "email": EMAIL,
            "purpose": auth.PURPOSE_ACCESS,
            "aud": "some-other-service",
            "iss": auth.JWT_ISSUER,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )
    with pytest.raises(HTTPException):
        auth.decode_access_token(foreign)


def test_access_token_without_purpose_is_rejected():
    from datetime import datetime, timedelta, timezone

    no_purpose = jwt.encode(
        {
            "sub": USER_ID,
            "email": EMAIL,
            "aud": auth.AUDIENCE_ACCESS,
            "iss": auth.JWT_ISSUER,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )
    with pytest.raises(HTTPException):
        auth.decode_access_token(no_purpose)


def test_valid_access_token_still_decodes():
    payload = auth.decode_access_token(auth.create_access_token(USER_ID, EMAIL))
    assert payload["sub"] == USER_ID
    assert payload["jti"]


# ─── Password rules (P0-small) ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "password,reason",
    [
        ("Ab1cdef", "too short"),
        ("abcdefgh", "no digit"),
        ("12345678", "no letter"),
        ("password1", "common"),
        ("a1" + "x" * 80, "over bcrypt's 72-byte ceiling"),
    ],
)
def test_bad_passwords_are_rejected(password, reason):
    assert auth.password_rule_violation(password) is not None, reason


def test_reasonable_password_is_accepted():
    assert auth.password_rule_violation("correct7horse") is None


def test_over_long_password_does_not_raise_on_verify():
    """bcrypt>=4 raises on >72 bytes rather than truncating, so an over-long
    login attempt used to surface as a 500 instead of a failed check."""
    stored = auth.hash_password("correct7horse")
    assert auth.verify_password("x" * 200, stored) is False
