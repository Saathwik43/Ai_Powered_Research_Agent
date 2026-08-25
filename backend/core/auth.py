import hashlib
import hmac
import os
import uuid
import bcrypt
import logging
from datetime import datetime, timedelta, timezone
import jwt
from jwt import InvalidTokenError as JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from core.database import db
from core.ttl_cache import TTLCache
from dotenv import load_dotenv
from google.oauth2 import id_token
from google.auth.transport import requests
from services import usage_tracker
from bson import ObjectId

load_dotenv()

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is not configured in the environment.")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID:
    logger.warning("GOOGLE_CLIENT_ID is not configured. Google Sign-In will not work.")
ALGORITHM = "HS256"

# 1.14. A 24h bearer token is a 24h window for anything that gets hold of one:
# there is no server-side state behind it, so `revoked_tokens` is the only way to
# end it early and nothing outside an explicit logout writes there. The session
# is now a short access token plus a long, *rotating* refresh token, so a stolen
# access token expires on its own and a stolen refresh token is detectable —
# using it after the real client already has means two holders, and the whole
# family is killed (see `rotate_refresh_token`).
ACCESS_TOKEN_EXPIRE_HOURS = float(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "1"))
REFRESH_TOKEN_EXPIRE_DAYS = float(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "14"))


# ─── Token audiences and signing keys ──────────────────────────────────────────
#
# A password-reset link used to be signed with the *same* key as a session
# token, so the only thing standing between "clicked a reset email" and "logged
# in as that user" was `decode_access_token` not looking at the `purpose` claim
# -- which it did not. Pasting the emailed token into the Authorization header
# was a full login, from a token that travels in plaintext in an inbox.
#
# Two independent barriers now:
#   1. a distinct signing key per token class, so a reset/verify token fails at
#      the *signature* check when presented as a bearer token, and
#   2. an explicit `purpose` + `aud` on every token, checked on decode.
#
# The derived keys are deterministic (HMAC of the configured secret) so no new
# environment variable is required to deploy this, but each can still be pinned
# explicitly if an operator wants independent rotation.

JWT_ISSUER = os.getenv("JWT_ISSUER", "research-agent")
AUDIENCE_ACCESS = "research-agent:access"
AUDIENCE_RESET = "research-agent:password-reset"
AUDIENCE_VERIFY = "research-agent:email-verify"
AUDIENCE_REFRESH = "research-agent:refresh"

PURPOSE_ACCESS = "access"
PURPOSE_RESET = "reset"
PURPOSE_VERIFY = "verify"
PURPOSE_REFRESH = "refresh"


def _derive_key(label: str) -> str:
    """A key that is cryptographically independent of SECRET_KEY."""
    return hmac.new(SECRET_KEY.encode(), label.encode(), hashlib.sha256).hexdigest()


RESET_SECRET_KEY = os.getenv("JWT_RESET_SECRET_KEY") or _derive_key("password-reset-v1")
VERIFY_SECRET_KEY = os.getenv("JWT_VERIFY_SECRET_KEY") or _derive_key("email-verify-v1")
REFRESH_SECRET_KEY = os.getenv("JWT_REFRESH_SECRET_KEY") or _derive_key("refresh-v1")

bearer_scheme = HTTPBearer()


# ─── Password Helpers ──────────────────────────────────────────────────────────

# bcrypt hashes at most 72 bytes and bcrypt>=4 raises rather than truncating, so
# an over-long password would surface as a 500 from signup instead of a 422.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


def password_rule_violation(password: str) -> str | None:
    """Return why *password* is unacceptable, or None if it is fine.

    Length alone was the whole rule, which let "password" through. Kept here
    rather than in the Pydantic model so the reset path enforces the same rule
    as signup -- they used to disagree.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes."
    if not any(c.isalpha() for c in password):
        return "Password must contain at least one letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit."
    if password.lower() in _COMMON_PASSWORDS:
        return "That password is too common. Please choose another."
    return None


_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890",
    "qwerty123", "iloveyou", "abc12345", "letmein1", "welcome1", "admin123",
    "football1", "sunshine1", "princess1", "monkey123", "passw0rd",
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        # Over-long candidate against a stored hash: a failed check, not a 500.
        return False


# ─── JWT Helpers ───────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str, family: str | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "jti": str(uuid.uuid4()),
        "purpose": PURPOSE_ACCESS,
        "aud": AUDIENCE_ACCESS,
        "iss": JWT_ISSUER,
    }
    if family:
        # Carried so logout can end the refresh family this token belongs to,
        # not just blacklist the one bearer token being handed back.
        payload["fam"] = family
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

RESET_TOKEN_EXPIRE_MINUTES = 30
VERIFY_TOKEN_EXPIRE_HOURS = 24

def create_reset_token(user_id: str, email: str , version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "email": email,
        "purpose": PURPOSE_RESET,
        "v": version,
        "exp": expire,
        "aud": AUDIENCE_RESET,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, RESET_SECRET_KEY, algorithm=ALGORITHM)

def decode_reset_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            RESET_SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=AUDIENCE_RESET,
            issuer=JWT_ISSUER,
        )
        if payload.get("purpose") != PURPOSE_RESET:
            raise JWTError("wrong token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")


def create_verification_token(user_id: str, email: str, version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "purpose": PURPOSE_VERIFY,
        "v": version,
        "exp": expire,
        "aud": AUDIENCE_VERIFY,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, VERIFY_SECRET_KEY, algorithm=ALGORITHM)


def decode_verification_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            VERIFY_SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=AUDIENCE_VERIFY,
            issuer=JWT_ISSUER,
        )
        if payload.get("purpose") != PURPOSE_VERIFY:
            raise JWTError("wrong token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")


def decode_access_token(token: str) -> dict:
    """Decode a *session* token. Rejects anything not minted by
    ``create_access_token``: a different key, audience, issuer or purpose all
    fail here rather than being waved through as a login."""
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=AUDIENCE_ACCESS,
            issuer=JWT_ISSUER,
        )
        if payload.get("purpose") != PURPOSE_ACCESS:
            raise JWTError("wrong token type")
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─── Refresh tokens with rotation and reuse detection (1.14) ───────────────────
#
# One refresh token is valid at a time. Presenting it returns a new access token
# *and* a new refresh token, and retires the one presented. A retired token being
# presented again means two parties hold it — the legitimate client and whoever
# copied it — and there is no way to tell which one is asking, so the entire
# family (every token descended from that login) is revoked and both are forced
# to sign in again. That is the standard OAuth 2.0 BCP treatment and the whole
# reason rotation is worth having.

def create_refresh_token(user_id: str, email: str, family: str, jti: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "jti": jti,
        "fam": family,
        "purpose": PURPOSE_REFRESH,
        "aud": AUDIENCE_REFRESH,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, REFRESH_SECRET_KEY, algorithm=ALGORITHM)


def decode_refresh_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            REFRESH_SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=AUDIENCE_REFRESH,
            issuer=JWT_ISSUER,
        )
        if payload.get("purpose") != PURPOSE_REFRESH:
            raise JWTError("wrong token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please log in again.")


async def issue_session(user_id: str, email: str) -> dict:
    """A fresh login: one access token and the first refresh token of a family."""
    family = str(uuid.uuid4())
    jti = str(uuid.uuid4())
    refresh = create_refresh_token(user_id, email, family, jti)
    await _record_refresh(user_id, family, jti)
    return {
        "token": create_access_token(user_id, email, family),
        "refresh_token": refresh,
        "expires_in": int(ACCESS_TOKEN_EXPIRE_HOURS * 3600),
    }


async def _record_refresh(user_id: str, family: str, jti: str) -> None:
    await db["refresh_tokens"].update_one(
        {"_id": jti},
        {"$set": {
            "_id": jti,
            "user_id": user_id,
            "family": family,
            "used": False,
            "created_at": datetime.now(timezone.utc),
            # TTL-indexed, so a family nobody returns to disappears on its own.
            "expires_at": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        }},
        upsert=True,
    )


async def revoke_refresh_family(family: str) -> int:
    """End every session descended from one login."""
    if not family:
        return 0
    result = await db["refresh_tokens"].update_many(
        {"family": family, "used": False},
        {"$set": {"used": True, "revoked": True}},
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def rotate_refresh_token(token: str) -> dict:
    """Exchange *token* for a new access + refresh pair.

    Raises 401 both when the token is unknown and when it has already been
    spent — the second case additionally kills the family, because a replayed
    refresh token is the signal that one has leaked.
    """
    payload = decode_refresh_token(token)
    jti = payload.get("jti")
    family = payload.get("fam")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise HTTPException(status_code=401, detail="Invalid session. Please log in again.")

    record = await db["refresh_tokens"].find_one({"_id": jti})
    if record is None:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    if record.get("used"):
        revoked = await revoke_refresh_family(family)
        logger.warning(
            "Refresh token reuse detected for user %s (family %s); revoked %d outstanding token(s).",
            user_id, family, revoked,
        )
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    user = await db["users"].find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    if user.get("status") == "suspended":
        await revoke_refresh_family(family)
        raise HTTPException(status_code=403, detail="Your account has been suspended by an administrator.")

    new_jti = str(uuid.uuid4())
    email = user.get("email") or payload.get("email") or ""
    refresh = create_refresh_token(user_id, email, family, new_jti)
    await _record_refresh(user_id, family, new_jti)
    # Retired only after its successor exists, so a crash between the two leaves
    # the client with a token that still works rather than a dead session.
    await db["refresh_tokens"].update_one(
        {"_id": jti}, {"$set": {"used": True, "replaced_by": new_jti}}
    )
    return {
        "token": create_access_token(user_id, email, family),
        "refresh_token": refresh,
        "expires_in": int(ACCESS_TOKEN_EXPIRE_HOURS * 3600),
        "user": {
            "id": user_id,
            "email": email,
            "name": user.get("name", ""),
            "role": user.get("role", "user"),
            "picture": user.get("picture"),
        },
    }


# ─── Token Revocation (logout blacklist) ───────────────────────────────────────
# Stores revoked jtis with their original expiry so Mongo can TTL-expire them
# automatically once the token would've expired naturally anyway.

async def revoke_token(jti: str, exp: int) -> None:
    if not jti:
        return
    collection = db["revoked_tokens"]
    await collection.update_one(
        {"jti": jti},
        {"$set": {"jti": jti, "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc)}},
        upsert=True,
    )


async def is_token_revoked(jti: str) -> bool:
    if not jti:
        return False
    collection = db["revoked_tokens"]
    doc = await collection.find_one({"jti": jti})
    return doc is not None


# ─── Auth Dependency ───────────────────────────────────────────────────────────

# The users document is read on **every** authenticated request, only for the
# role, the display name and the suspended flag. Those change rarely; the read
# does not. A short TTL keeps a suspension or a role change taking effect within
# the minute while removing the round trip from the hot path — and admin actions
# call `invalidate_user_cache` so they take effect immediately rather than after
# it expires (1.14).
USER_CACHE_TTL = 60.0
_user_cache = TTLCache(maxsize=5000, ttl=USER_CACHE_TTL)


def invalidate_user_cache(user_id: str | None = None) -> None:
    """Drop one cached user, or all of them when *user_id* is None."""
    if user_id is None:
        _user_cache.clear()
    else:
        _user_cache.pop(str(user_id), None)


async def _load_user(user_id: str) -> dict | None:
    cached = _user_cache.get(user_id)
    if cached is not None:
        return cached or None  # {} is the cached form of "no such user"

    try:
        user = await db["users"].find_one({"_id": ObjectId(user_id)})
    except Exception as e:
        # A malformed id, or Mongo being unreachable. Neither is a reason to
        # cache anything; the caller degrades to the token's own claims.
        logger.warning("User lookup failed for %s: %s", user_id, e)
        return None
    _user_cache[user_id] = user or {}
    return user


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    if await is_token_revoked(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Set the current user ID for usage tracking in the current async context
    usage_tracker.current_user_id.set(user_id)

    # Also attach role if possible, but standard token only has email.
    user = await _load_user(user_id)
    if user and user.get("status") == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended by an administrator."
        )
    
    role = user.get("role", "user") if user else "user"
    name = user.get("name", "") if user else ""
    picture = user.get("picture") if user else None
    
    return {"user_id": user_id, "email": payload.get("email"), "role": role, "name": name, "picture": picture}


# ─── Signup / email verification ───────────────────────────────────────────────

VERIFICATION_MESSAGE = (
    "Account created. Check your email for a verification link — you can sign in "
    "once your address is confirmed."
)


async def _send_verification(user_id: str, email: str, version: int) -> bool:
    """Mail a verification link. Returns whether it was actually sent.

    Never raises: a mail-provider outage must not lose the account that was just
    created, and must not tell an enumerator which addresses exist.
    """
    from core.email_utils import EmailSendError, send_verification_email

    token = create_verification_token(user_id, email, version)
    try:
        await send_verification_email(email, token)
        return True
    except EmailSendError as e:
        logger.error(f"Verification email failed for {email}: reason={e.reason} detail={e.detail}")
        return False


async def signup_user(email: str, password: str, name: str) -> dict:
    """Create an *unverified* account and mail a verification link.

    No session token is returned. Handing one out at signup meant an address
    nobody had proved control of was already a working account -- which is also
    what made the Google-link hijack in `google_auth_user` reachable.
    """
    violation = password_rule_violation(password)
    if violation:
        raise HTTPException(status_code=400, detail=violation)

    collection = db["users"]
    existing = await collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    hashed = hash_password(password)
    user_doc = {
        "email": email,
        "name": name,
        "password": hashed,
        "email_verified": False,
        "verify_token_version": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await collection.insert_one(user_doc)
    user_id = str(result.inserted_id)

    sent = await _send_verification(user_id, email, 0)
    return {
        "message": VERIFICATION_MESSAGE,
        "requires_verification": True,
        "email": email,
        "email_sent": sent,
    }


async def resend_verification_email(email: str) -> dict:
    """Re-mail the verification link. Deliberately uniform in its reply so it
    cannot be used to test which addresses are registered."""
    user = await db["users"].find_one({"email": email})
    if user and not user.get("email_verified") and user.get("password"):
        version = int(user.get("verify_token_version", 0) or 0)
        await _send_verification(str(user["_id"]), email, version)
    return {"message": "If that address needs verifying, a new link has been sent."}


async def verify_email_with_token(token: str) -> dict:
    """Activate the account named by a verification token and sign the user in."""
    payload = decode_verification_token(token)
    collection = db["users"]
    user = await collection.find_one({"_id": ObjectId(payload["sub"])})
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    if int(user.get("verify_token_version", 0) or 0) != int(payload.get("v", -1)):
        raise HTTPException(status_code=400, detail="This verification link has already been used or is invalid.")

    if not user.get("email_verified"):
        await collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"email_verified": True}, "$inc": {"verify_token_version": 1}},
        )
        invalidate_user_cache(str(user["_id"]))

    user_id = str(user["_id"])
    email = user["email"]
    session = await issue_session(user_id, email)
    return {
        **session,
        "user": {
            "id": user_id,
            "email": email,
            "name": user.get("name", ""),
            "role": user.get("role", "user"),
        },
    }


# ─── Login ─────────────────────────────────────────────────────────────────────

async def login_user(email: str, password: str) -> dict:
    collection = db["users"]
    user = await collection.find_one({"email": email})
    if not user or not user.get("password") or not verify_password(password, user["password"]):
        # If user exists but has no password, they likely signed up via Google
        if user and not user.get("password"):
            raise HTTPException(status_code=401, detail="This account was created using Google Sign-In. Please sign in with Google.")
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    # Checked only after the password matches, so the message cannot be used to
    # enumerate which addresses have pending signups.
    if not user.get("email_verified", False):
        raise HTTPException(
            status_code=403,
            detail="Please verify your email address before signing in. Check your inbox for the verification link.",
        )

    user_id = str(user["_id"])
    session = await issue_session(user_id, email)
    return {**session, "user": {"id": user_id, "email": email, "name": user.get("name", ""), "role": user.get("role", "user")}}


# ─── Password Reset ────────────────────────────────────────────────────────────

async def request_password_reset(email: str):
    from core.email_utils import send_reset_email, EmailSendError
    collection = db["users"]
    user = await collection.find_one({"email": email})
    if user:
        version = user.get("reset_token_version", 0)
        token = create_reset_token(str(user["_id"]), email, version)
        try:
            await send_reset_email(email, token)
        except EmailSendError as e:
            logger.error(f"Password reset email failed for {email}: reason={e.reason} detail={e.detail}")
    return {"message": "If that email exists, a reset link has been sent."}

async def is_reset_token_valid(token: str) -> bool:
    try:
        payload = decode_reset_token(token)
    except HTTPException:
        return False
    user = await db["users"].find_one({"_id": ObjectId(payload["sub"])})
    if not user or user.get("reset_token_version", 0) != payload.get("v", -1):
        return False
    return True
    
async def reset_password_with_token(token: str, new_password: str):
    violation = password_rule_violation(new_password)
    if violation:
        raise HTTPException(status_code=400, detail=violation)

    payload = decode_reset_token(token)
    collection = db["users"]
    user = await collection.find_one({"_id": ObjectId(payload["sub"])})
    if not user or user.get("reset_token_version", 0) != payload.get("v", -1):
        raise HTTPException(status_code=400, detail="This reset link has already been used or is invalid.")
    # Completing a reset proves control of the mailbox, which is the same thing
    # the verification mail asks for -- so an account that only ever stalled at
    # "unverified" becomes usable here rather than dead-ending at login.
    await collection.update_one(
        {"_id": ObjectId(payload["sub"])},
        {
            "$set": {"password": hash_password(new_password), "email_verified": True},
            "$inc": {"reset_token_version": 1},
        },
    )
    return {"message": "Password updated. Please log in."}



# ─── Google Auth ───────────────────────────────────────────────────────────────

def verify_google_token(token: str) -> dict:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google authentication is not configured on the server.")
    try:
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        return idinfo
    except ValueError as e:
        logger.error(f"Google token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid Google token.")

async def google_auth_user(email: str, name: str, picture: str = None) -> dict:
    """Sign in (or register) via a *verified* Google identity.

    The caller must have already confirmed Google's `email_verified` claim --
    see the `/api/auth/google` route.

    The account-takeover this guards against: an attacker signs up with the
    victim's address and a password they know, never verifies it, and waits.
    The victim later clicks "Sign in with Google" and the old code handed them a
    session on that *same* document -- with the attacker's password still
    attached, so everything the victim then wrote was readable by logging in
    with it. An unverified password is not evidence of ownership, so it is
    discarded here rather than linked; the outstanding reset version is bumped
    so any reset link the attacker is holding dies with it.
    """
    collection = db["users"]
    user = await collection.find_one({"email": email})

    if not user:
        # Create a new user account without a password for Google Sign-in users
        user_doc = {
            "email": email,
            "name": name,
            "role": "user",
            "auth_provider": "google",
            "picture": picture,
            "email_verified": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = await collection.insert_one(user_doc)
        user_id = str(result.inserted_id)
        role = "user"
    else:
        user_id = str(user["_id"])
        role = user.get("role", "user")

        update_fields: dict = {"email_verified": True}
        unset_fields: dict = {}
        inc_fields: dict = {}

        has_password = bool(user.get("password"))
        if has_password and not user.get("email_verified", False):
            logger.warning(
                "Google sign-in for %s dropped an unverified password credential on "
                "the existing account (possible pre-registration takeover attempt).",
                email,
            )
            unset_fields["password"] = ""
            inc_fields["reset_token_version"] = 1
            has_password = False

        if not has_password:
            # Only a passwordless account is genuinely "a Google account".
            update_fields["auth_provider"] = "google"
        else:
            # Verified local account: linking is safe, but the password still
            # works, so don't relabel how it authenticates.
            update_fields["google_linked"] = True

        if picture and user.get("picture") != picture:
            update_fields["picture"] = picture

        update: dict = {"$set": update_fields}
        if unset_fields:
            update["$unset"] = unset_fields
        if inc_fields:
            update["$inc"] = inc_fields
        await collection.update_one({"_id": user["_id"]}, update)
        invalidate_user_cache(user_id)
        picture = user.get("picture") or picture # Default to existing if not provided by Google

    session = await issue_session(user_id, email)
    return {**session, "user": {"id": user_id, "email": email, "name": name, "role": role, "picture": picture}}


# ─── Seed Admin ────────────────────────────────────────────────────────────────

async def seed_admin():
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    admin_name = os.getenv("ADMIN_NAME", "Admin")

    if not admin_email or not admin_password:
        return

    collection = db["users"]
    existing = await collection.find_one({"email": admin_email})
    if not existing:
        hashed = hash_password(admin_password)
        await collection.insert_one({
            "email": admin_email,
            "name": admin_name,
            "password": hashed,
            "role": "admin",
            # Configured out-of-band by an operator, not self-asserted by a
            # visitor, so there is nothing for a verification mail to prove.
            "email_verified": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Admin user seeded: {admin_email}")
    else:
        logger.info(f"Admin user already exists: {admin_email}")
        if not existing.get("email_verified"):
            await collection.update_one({"_id": existing["_id"]}, {"$set": {"email_verified": True}})


async def backfill_email_verified() -> int:
    """Grandfather accounts that predate email verification.

    Login now requires `email_verified`, and every account created before this
    change lacks the field. Without this one-time backfill the fix would lock
    out the entire existing user base on deploy. New signups are written with
    `email_verified: False` explicitly, so they are never matched here.
    """
    try:
        result = await db["users"].update_many(
            {"email_verified": {"$exists": False}},
            {"$set": {"email_verified": True}},
        )
        if result.modified_count:
            logger.info(
                "Grandfathered %s pre-existing account(s) as email-verified.",
                result.modified_count,
            )
        return int(result.modified_count)
    except Exception as e:
        logger.warning("Could not backfill email_verified: %s", e)
        return 0
