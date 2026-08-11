"""Signup, login, Google OAuth, logout, and password reset."""

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import (
    decode_access_token,
    get_current_user,
    google_auth_user,
    is_reset_token_valid,
    login_user,
    request_password_reset,
    reset_password_with_token,
    revoke_token,
    signup_user,
    verify_google_token,
)
from core.limiter import limiter
from schemas import (
    ForgotPasswordPayload,
    GoogleAuthPayload,
    LoginPayload,
    ResetPasswordPayload,
    SignupPayload,
)

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
    return await google_auth_user(email.lower(), name, picture)

@router.post("/api/auth/logout")
@limiter.limit("20/minute")
async def logout(request: Request, current_user: dict = Depends(get_current_user)):
    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    payload = decode_access_token(token)
    await revoke_token(payload.get("jti"), payload.get("exp"))
    return {"message": "Logged out."}

@router.post("/api/auth/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(request: Request, payload: ForgotPasswordPayload):
    return await request_password_reset(payload.email.strip().lower())

@router.post("/api/auth/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, payload: ResetPasswordPayload):
    return await reset_password_with_token(payload.token, payload.new_password)

@router.get("/api/auth/validate-reset-token")
@limiter.limit("20/minute")
async def validate_reset_token(request: Request, token: str):
    return {"valid": await is_reset_token_valid(token)}

@router.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}
