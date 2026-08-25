"""A hard ceiling on request body size, enforced before the body is buffered.

Pure ASGI rather than ``BaseHTTPMiddleware`` on purpose: ``BaseHTTPMiddleware``
runs the downstream app in a separate task, which breaks contextvars set inside
an endpoint from being visible while a ``StreamingResponse`` body is iterated —
the exact mechanism `usage_tracker.current_user_id` depends on for the
manuscript SSE route (see ``routers/manuscript._sse_wrap``).

Two checks, because either alone is bypassable:

* ``Content-Length``, so an oversized body is refused before a single byte of it
  is read, and
* a running byte count over the received chunks, because a chunked
  (``Transfer-Encoding: chunked``) request declares no length at all.
"""

import json
import logging

logger = logging.getLogger(__name__)

__all__ = ["BodySizeLimitMiddleware"]


def _too_large_response(limit: int) -> dict:
    body = json.dumps(
        {"detail": f"Request body too large. Limit is {limit // (1024 * 1024)}MB."}
    ).encode()
    return {
        "start": {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        },
        "body": {"type": "http.response.body", "body": body},
    }


class BodySizeLimitMiddleware:
    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            logger.info(
                "Rejected %s %s: Content-Length %s exceeds %s",
                scope.get("method"), scope.get("path"), declared, self.max_body_bytes,
            )
            await self._reject(send)
            return

        received = 0
        overflowed = False

        async def counting_receive():
            nonlocal received, overflowed
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    overflowed = True
                    # Cut the stream off rather than keep buffering. The handler
                    # then fails on a truncated body; `guarded_send` replaces
                    # whatever it decided to say with the real reason.
                    return {"type": "http.disconnect"}
            return message

        answered = False

        async def guarded_send(message):
            nonlocal answered
            if answered:
                # Already sent the 413; drop the handler's own response, which
                # would otherwise be a confusing 400 "malformed JSON" about a
                # body we deliberately cut in half.
                return
            if message["type"] == "http.response.start" and overflowed:
                answered = True
                logger.info(
                    "Rejected %s %s: streamed body exceeded %s bytes",
                    scope.get("method"), scope.get("path"), self.max_body_bytes,
                )
                await self._reject(send)
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

        if overflowed and not answered:
            # The handler never responded at all (it propagated the disconnect).
            logger.info(
                "Rejected %s %s: streamed body exceeded %s bytes",
                scope.get("method"), scope.get("path"), self.max_body_bytes,
            )
            await self._reject(send)

    async def _reject(self, send):
        response = _too_large_response(self.max_body_bytes)
        await send(response["start"])
        await send(response["body"])


def _declared_length(scope) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
