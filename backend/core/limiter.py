"""Shared slowapi limiter.

Lives here rather than in ``main`` so routers can decorate their endpoints
without importing the app module (which would be circular).
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from core.auth import decode_access_token


def get_user_id_for_rate_limit(request: Request) -> str:
    """Rate-limit per authenticated user, falling back to remote address."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            payload = decode_access_token(token)
            user_id = payload.get("sub")
            if user_id:
                return user_id
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=get_user_id_for_rate_limit)
