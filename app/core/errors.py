"""Handlers globais de erro: envelope JSON padronizado com request_id."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id import get_request_id
from app.core.responses import UTF8JSONResponse

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """Base application error carrying an error code and HTTP status."""

    def __init__(self, code: str, message: str, status_code: int = 500):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _envelope(code: str, message: str, status_code: int, details: list | None = None):
    body: dict = {
        "error": {
            "code": code,
            "message": message,
            "request_id": get_request_id(),
        }
    }
    if details:
        body["error"]["details"] = details
    return UTF8JSONResponse(status_code=status_code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return _envelope(exc.code, exc.message, exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Standardized message per status: never expose internal `detail`.
        code, message = {
            404: ("not_found", "Resource not found."),
            405: ("method_not_allowed", "Method not allowed."),
            401: ("unauthorized", "Authentication required."),
            403: ("forbidden", "Access denied."),
            413: ("payload_too_large", "Request body too large."),
            415: ("unsupported_media_type", "Content-Type must be application/json."),
            429: ("rate_limited", "Too many requests."),
        }.get(exc.status_code, ("http_error", "Request could not be processed."))
        if exc.status_code >= 500:
            logger.error("HTTPException %s: %s", exc.status_code, exc.detail)
        return _envelope(code, message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {
                "field": ".".join(str(loc) for loc in err["loc"] if loc != "body"),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return _envelope("validation_error", "Invalid request payload.", 422, details)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return _envelope("internal_error", "An unexpected error occurred.", 500)
