"""Request-ID por requisicao via ContextVar (propagado a logs e respostas)."""

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> str:
    rid = value or str(uuid.uuid4())
    _request_id.set(rid)
    return rid
