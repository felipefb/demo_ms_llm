"""Structured logging (structlog) with JSON output and request_id correlation.

- LOG_FORMAT=json (default) emits one JSON object per line; LOG_FORMAT=console
  emits a human-friendly colored line for local development.
- LOG_LEVEL controls the root level (stdlib and structlog share it).
- Every record — from structlog OR plain stdlib loggers — is enriched with the
  current request_id (contextvar set by the ASGI middleware).
"""

import logging
import sys

import structlog

from app.core.request_id import get_request_id


def _add_request_id(_logger, _method_name, event_dict: dict) -> dict:
    event_dict.setdefault("request_id", get_request_id() or "-")
    return event_dict


def setup_logging(level: str = "INFO", log_format: str = "json") -> None:
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _add_request_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Avoid duplicated access logs: ours (middleware) is the source of truth.
    logging.getLogger("uvicorn.access").disabled = True
