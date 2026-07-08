"""API key authentication (X-API-Key) as a pure ASGI middleware.

The plaintext key is NEVER stored server-side: the env var API_KEY_HASH holds
the SHA-256 hex digest of the key. The incoming header is hashed and compared
with `hmac.compare_digest` (constant time). Public paths (/health, /ready,
docs) are exempt. In dev/test with no API_KEY_HASH configured, auth is
disabled with a warning; in prod-like envs config fail-fast requires the hash.
"""

import hashlib
import hmac
import json
import logging

from app.core.config import Settings
from app.core.request_id import get_request_id

logger = logging.getLogger("app.auth")

API_KEY_HEADER = b"x-api-key"

# Exact public paths (never require a key). /metrics is meant for the
# Prometheus scraper on the internal network (never expose it publicly).
PUBLIC_PATHS = {"/health", "/ready", "/docs", "/redoc", "/openapi.json", "/metrics"}


def hash_api_key(key: str) -> str:
    """SHA-256 hex digest used to compare against API_KEY_HASH."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def mask_key(key: str) -> str:
    """Safe representation of a key for logs: first 4 chars + length."""
    if not key:
        return "<empty>"
    return f"{key[:4]}***(len={len(key)})"


def _error_response(status: int, code: str, message: str, extra_headers: list | None = None):
    body = json.dumps(
        {"error": {"code": code, "message": message, "request_id": get_request_id()}}
    ).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return status, headers, body


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


class ApiKeyAuthMiddleware:
    """Rejects requests without a valid X-API-Key (401), except public paths."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.enabled = bool(settings.api_key_hash)
        if not self.enabled:
            logger.warning(
                "API key auth DISABLED (API_KEY_HASH not set) — acceptable only in dev/test."
            )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if is_public_path(path):
            await self.app(scope, receive, send)
            return

        provided = ""
        for name, value in scope.get("headers", []):
            if name == API_KEY_HEADER:
                provided = value.decode("latin-1")
                break

        expected_hash = self.settings.api_key_hash
        if not provided or not hmac.compare_digest(hash_api_key(provided), expected_hash):
            logger.warning(
                "Rejected request to %s: %s API key (%s)",
                path,
                "invalid" if provided else "missing",
                mask_key(provided),
            )
            status, headers, body = _error_response(401, "unauthorized", "Authentication required.")
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
