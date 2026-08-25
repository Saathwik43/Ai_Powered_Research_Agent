"""Signup, login, Google OAuth, logout, email verification, and password reset."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import (
    decode_access_token,
    get_current_user,
    google_auth_user,
    is_reset_token_valid,
    login_user,
    request_password_reset,
    resend_verification_email,
    reset_password_with_token,
    revoke_refresh_family,
    revoke_token,
    rotate_refresh_token,
    signup_user,
    verify_email_with_token,
    verify_google_token,
)
from core.limiter import limiter
from schemas import (
    ForgotPasswordPayload,
    GoogleAuthPayload,
    LoginPayload,
    RefreshPayload,
    ResendVerificationPayload,
    ResetPasswordPayload,
    SignupPayload,
    TokenPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


@router.post("/api/auth/signup")
@limiter.limit("5/minute")
async def signup(request: Request, payload: SignupPayload):
    email = payload.email.strip().lower()
    return await signup_user(email, payload.password, payload.name.strip())

@router.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginPayload):
    email = payload.email.strip().lower()
    return await login_user(email, payload.password)

@router.post("/api/auth/google")
@limiter.limit("10/minute")
async def google_auth(request: Request, payload: GoogleAuthPayload):
    idinfo = verify_google_token(payload.token)
    email = idinfo.get('email')
    name = idinfo.get('name', 'Google User')
    picture = idinfo.get('picture')
    if not email:
        raise HTTPException(status_code=400, detail="Google token does not contain an email.")
    # A Google account can carry an address it never confirmed (this is true of
    # Workspace domains that skip verification). Treating that as proof of
    # ownership would reopen exactly the takeover `google_auth_user` closes.
    if idinfo.get("email_verified") is not True:
        logger.warning("Rejected Google sign-in for %s: email_verified claim absent/false.", email)
        raise HTTPException(
            status_code=403,
            detail="Your Google account has not verified this email address.",
        )
    return await google_auth_user(email.lower(), name, picture)

@router.post("/api/auth/refresh")
# Deliberately generous: a tab that has been open overnight refreshes on its
# first action, and several tabs of the same session may do it at once. Reuse
# detection, not the rate limit, is what makes a stolen token useless.
@limiter.limit("30/minute")
async def refresh_session(request: Request, payload: RefreshPayload):
    """Exchange a refresh token for a new access + refresh pair (1.14).

    Not behind `get_current_user`: the point of calling this is that the access
    token has expired.
    """
    return await rotate_refresh_token(payload.refresh_token)


@router.post("/api/auth/logout")
@limiter.limit("20/minute")
async def logout(request: Request, current_user: dict = Depends(get_current_user)):
    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    payload = decode_access_token(token)
    await revoke_token(payload.get("jti"), payload.get("exp"))
    # Blacklisting the bearer token alone would leave the refresh token live, so
    # "log out" would last until the access token expired and then silently
    # renew itself.
    await revoke_refresh_family(payload.get("fam"))
    return {"message": "Logged out."}

@router.post("/api/auth/verify-email")
@limiter.limit("10/minute")
async def verify_email(request: Request, payload: TokenPayload):
    return await verify_email_with_token(payload.token)

@router.post("/api/auth/resend-verification")
@limiter.limit("3/minute")
async def resend_verification(request: Request, payload: ResendVerificationPayload):
    return await resend_verification_email(payload.email.strip().lower())

@router.post("/api/auth/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(request: Request, payload: ForgotPasswordPayload):
    return await request_password_reset(payload.email.strip().lower())

@router.post("/api/auth/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, payload: ResetPasswordPayload):
    return await reset_password_with_token(payload.token, payload.new_password)

@router.post("/api/auth/validate-reset-token")
@limiter.limit("20/minute")
async def validate_reset_token(request: Request, payload: TokenPayload):
    """POST, not GET: a reset token in the query string is written to browser
    history, leaks through `Referer` on any third-party asset the page loads,
    and is recorded verbatim by every proxy access log in front of the API."""
    return {"valid": await is_reset_token_valid(payload.token)}

@router.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}
