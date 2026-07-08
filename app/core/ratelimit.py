"""In-memory rate limiting per (API key, client IP) — pure ASGI middleware.

Fixed sliding-window (deque of timestamps) per key. Limits are configurable
via RATE_LIMIT_REQUESTS / RATE_LIMIT_WINDOW_SECONDS; disable with
RATE_LIMIT_ENABLED=false (tests). Exceeding the limit returns 429 with a
Retry-After header and the standard error envelope.

Note: in-memory state is per-process. For multi-replica deployments the
production design (docs/architecture.md) moves this to API Gateway / Redis.
"""

import hashlib
import logging
import time
from collections import deque

from app.core.auth import _error_response, is_public_path
from app.core.config import Settings
from app.core.metrics import rate_limit_rejections_total

logger = logging.getLogger("app.ratelimit")

_MAX_TRACKED_KEYS = 10_000  # bound memory under key/IP churn


class RateLimitMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.enabled = settings.rate_limit_enabled
        self.max_requests = settings.rate_limit_requests
        self.window = settings.rate_limit_window_seconds
        self._hits: dict[str, deque[float]] = {}

    def _client_key(self, scope) -> str:
        api_key = ""
        for name, value in scope.get("headers", []):
            if name == b"x-api-key":
                api_key = value.decode("latin-1")
                break
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        # Hash so raw API keys never sit in memory dumps/logs via this dict.
        digest = hashlib.sha256(f"{api_key}|{ip}".encode()).hexdigest()[:32]
        return digest

    def _check(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        bucket = self._hits.get(key)
        if bucket is None:
            if len(self._hits) >= _MAX_TRACKED_KEYS:
                self._hits.clear()  # crude but bounded; resets all windows
            bucket = deque()
            self._hits[key] = bucket
        cutoff = now - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            retry_after = max(1, int(bucket[0] + self.window - now) + 1)
            return False, retry_after
        bucket.append(now)
        return True, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return
        if is_public_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        allowed, retry_after = self._check(self._client_key(scope))
        if not allowed:
            rate_limit_rejections_total.labels(path=scope.get("path", "unknown")).inc()
            logger.warning(
                "Rate limit exceeded on %s (retry_after=%ss)", scope.get("path"), retry_after
            )
            status, headers, body = _error_response(
                429,
                "rate_limited",
                "Too many requests.",
                extra_headers=[(b"retry-after", str(retry_after).encode("ascii"))],
            )
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
