"""Response security headers + request body/content-type limits (pure ASGI).

- SecurityHeadersMiddleware: adds conservative security headers to every
  response (API-only service, so a restrictive CSP is safe; /docs paths get a
  relaxed CSP so Swagger UI keeps working when enabled).
- BodyLimitMiddleware: rejects bodies over MAX_BODY_BYTES with 413 (checks
  Content-Length upfront and also counts streamed/chunked bytes), and rejects
  non-JSON content types on write methods with 415.
"""

import json
import logging

from app.core.request_id import get_request_id

logger = logging.getLogger("app.security")

_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

_BASE_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
_API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in _BASE_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                # Swagger UI needs inline scripts/styles from CDN; keep the
                # strict CSP for every non-docs (API) response.
                if path not in _DOCS_PATHS and _API_CSP[0] not in existing:
                    headers.append(_API_CSP)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _envelope_bytes(status: int, code: str, message: str):
    body = json.dumps(
        {"error": {"code": code, "message": message, "request_id": get_request_id()}}
    ).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return status, headers, body


async def _reject(send, status: int, code: str, message: str):
    status, headers, body = _envelope_bytes(status, code, message)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    """413 for oversized bodies; 415 for unexpected content types on writes."""

    WRITE_METHODS = {"POST", "PUT", "PATCH"}

    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        headers = dict(scope.get("headers", []))

        if method in self.WRITE_METHODS:
            content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
            if content_type and not content_type.startswith("application/json"):
                await _reject(
                    send, 415, "unsupported_media_type", "Content-Type must be application/json."
                )
                return

        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    logger.warning(
                        "Rejected oversized body (Content-Length=%s)", int(content_length)
                    )
                    await _reject(send, 413, "payload_too_large", "Request body too large.")
                    return
            except ValueError:
                await _reject(send, 400, "bad_request", "Invalid Content-Length header.")
                return

        # Also enforce while streaming (chunked bodies without Content-Length).
        received = 0
        limit = self.max_body_bytes
        exceeded = {"flag": False}

        async def receive_wrapper():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded["flag"] = True
                    # Truncate: signal end of body so the app stops reading.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        started = {"flag": False}

        async def send_wrapper(message):
            if exceeded["flag"] and not started["flag"]:
                started["flag"] = True
                await _reject(send, 413, "payload_too_large", "Request body too large.")
                return
            if message["type"] == "http.response.start":
                started["flag"] = True
            if not exceeded["flag"]:
                await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)
