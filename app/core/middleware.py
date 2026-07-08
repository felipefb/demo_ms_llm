"""Pure-ASGI request middleware (no BaseHTTPMiddleware).

Responsibilities, guaranteed even when the app raises an unhandled exception
(try/except/finally around the downstream call):

- request_id: read `X-Request-ID` or generate one, store it in a contextvar
  and echo it back on the response (including 500s from unhandled errors).
- latency: `X-Response-Time-Ms` header + Prometheus HTTP metrics.
- access log: one structured JSON line per request with request_id, method,
  route, status, latency and — never the body itself — only the request body
  size and its sha256 (privacy: prompts are not logged in production).
"""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable

import structlog

from app.core.metrics import http_request_duration_seconds, http_requests_total
from app.core.request_id import set_request_id

logger = structlog.stdlib.get_logger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"


def _route_template(scope: dict) -> str:
    """Low-cardinality path label: the matched route template, else "unmatched".

    Never the raw path: 404s (and 401s, which short-circuit before routing)
    would otherwise let an unauthenticated scanner mint unbounded label values
    and reflect arbitrary paths on the public /metrics output.
    """
    route = scope.get("route")
    path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
    return path_format or "unmatched"


class RequestIdMiddleware:
    """ASGI middleware: request_id + timing headers + JSON access log + metrics."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict,
        receive: Callable[[], Awaitable[dict]],
        send: Callable[[dict], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        rid = set_request_id(headers.get(REQUEST_ID_HEADER.lower()))
        start = time.perf_counter()

        status_code = 500
        response_started = False
        body_size = 0
        body_hash = hashlib.sha256()

        async def receive_wrapper() -> dict:
            nonlocal body_size
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                body_size += len(chunk)
                body_hash.update(chunk)
            return message

        def _timing_headers() -> list[tuple[bytes, bytes]]:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return [
                (REQUEST_ID_HEADER.encode("latin-1"), rid.encode("latin-1")),
                (b"x-response-time-ms", f"{elapsed_ms:.1f}".encode("latin-1")),
            ]

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + _timing_headers()
            await send(message)

        error: BaseException | None = None
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BaseException as exc:  # noqa: BLE001 - logged and handled below
            error = exc
            status_code = 500
            if response_started:
                raise
            # Guarantee X-Request-ID even on unhandled exceptions: emit the
            # 500 ourselves instead of letting the server error handler do it.
            body = json.dumps(
                {"error": {"code": "internal_error", "message": "Internal server error."}}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                        *_timing_headers(),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            path_label = _route_template(scope)
            method = scope.get("method", "GET")
            http_requests_total.labels(
                method=method, path=path_label, status=str(status_code)
            ).inc()
            http_request_duration_seconds.labels(
                method=method, path=path_label, status=str(status_code)
            ).observe(elapsed_ms / 1000)
            log = logger.error if error is not None else logger.info
            log(
                "http_request",
                method=method,
                path=scope.get("path", ""),
                route=path_label,
                status_code=status_code,
                latency_ms=round(elapsed_ms, 1),
                request_id=rid,
                client=(scope.get("client") or ("-",))[0],
                body_bytes=body_size,
                body_sha256=body_hash.hexdigest() if body_size else None,
                exc_info=error if error is not None else None,
            )
