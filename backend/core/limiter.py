"""Shared slowapi limiter.

Lives here rather than in ``main`` so routers can decorate their endpoints
without importing the app module (which would be circular).

Two things are configured here:

* **Who is being limited** (0.15). On Render every request arrives from the
  platform's proxy, so ``request.client.host`` is the *same* address for every
  visitor on the internet and the anonymous login limit was effectively one
  shared bucket. ``X-Forwarded-For`` carries the real client, but only the hops
  the proxy appended can be trusted — the leftmost entries are attacker-supplied
  and picking one would let anybody mint a fresh bucket per request. So the hop
  count is configured deliberately rather than guessed.

* **A default limit for every route** (0.6). Routes opt *out* by declaring their
  own ``@limiter.limit``; nothing is unlimited by omission any more.
"""

import logging
import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from core.auth import decode_access_token

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Number of proxies between the internet and this process that append to
# X-Forwarded-For. Render, Fly and a single nginx are all 1. Set to 2 if you put
# Cloudflare in front of one of those. 0 (the default) ignores the header
# entirely, which is the only safe assumption when the app is directly exposed.
TRUSTED_PROXY_HOPS = max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0") or 0))

# Applied to every route that does not declare its own limit. Generous enough
# that normal UI use never sees it; low enough that an unauthenticated scraper
# does not get a free run at the whole API surface.
DEFAULT_RATE_LIMIT = os.getenv("DEFAULT_RATE_LIMIT", "120/minute")


def client_ip(request: Request) -> str:
    """The caller's address, honouring ``X-Forwarded-For`` only as far as the
    configured number of trusted hops."""
    if TRUSTED_PROXY_HOPS > 0:
        forwarded = request.headers.get("X-Forwarded-For", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if chain:
            # Count in from the right: those entries were written by our own
            # proxies. Anything further left is whatever the client sent.
            index = max(0, len(chain) - TRUSTED_PROXY_HOPS)
            return chain[index]
    return get_remote_address(request)


def get_user_id_for_rate_limit(request: Request) -> str:
    """Rate-limit per authenticated user, falling back to the client address."""
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
    return client_ip(request)


limiter = Limiter(
    key_func=get_user_id_for_rate_limit,
    default_limits=[DEFAULT_RATE_LIMIT],
)

if TRUSTED_PROXY_HOPS == 0:
    logger.info(
        "Rate limiting keyed on the direct peer address. Behind a proxy "
        "(Render/nginx/Cloudflare) set TRUSTED_PROXY_HOPS or every anonymous "
        "caller shares one bucket."
    )
else:
    logger.info("Rate limiting trusts %s X-Forwarded-For hop(s).", TRUSTED_PROXY_HOPS)
