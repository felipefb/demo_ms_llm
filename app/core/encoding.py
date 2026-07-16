"""Normalização de charset do corpo JSON para UTF-8 (pure ASGI).

Clientes Windows (PowerShell 5.1 `Invoke-RestMethod`, curl em terminal cp1252)
enviam JSON com acentos codificado em cp1252/latin-1, geralmente sem declarar
`charset` no Content-Type. `json.loads` exige UTF-8, e o FastAPI converte a
falha de decodificação em um 400 genérico — antes de qualquer lógica da
aplicação (guardrail incluído): "eleições" em cp1252 virava 400 em vez de
`status=blocked`.

Este middleware roda nas escritas JSON, com o corpo já limitado pelo
BodyLimitMiddleware (que fica mais externo na cadeia):

1. `charset` declarado no Content-Type é respeitado;
2. corpo UTF-8 válido passa intocado (caminho comum, custo de um decode);
3. caso contrário, decodifica como cp1252 (fallback latin-1, que nunca falha)
   e re-encoda em UTF-8.

O handler recebe sempre UTF-8 válido; JSON sintaticamente inválido continua
caindo na validação normal (422).
"""

import logging

logger = logging.getLogger("app.encoding")

_WRITE_METHODS = {"POST", "PUT", "PATCH"}


def _declared_charset(content_type: str) -> str | None:
    for param in content_type.split(";")[1:]:
        name, _, value = param.strip().partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip('"').lower() or None
    return None


def _to_utf8(body: bytes, charset: str | None) -> bytes:
    if charset is not None and charset not in ("utf-8", "utf8"):
        try:
            return body.decode(charset).encode("utf-8")
        except (LookupError, UnicodeDecodeError):
            logger.warning("declared charset %r invalid for body; trying fallbacks", charset)
    try:
        body.decode("utf-8")
        return body
    except UnicodeDecodeError:
        pass
    try:
        decoded = body.decode("cp1252")
    except UnicodeDecodeError:
        decoded = body.decode("latin-1")
    logger.info("request body re-encoded to UTF-8 (client sent cp1252/latin-1)")
    return decoded.encode("utf-8")


class Utf8BodyMiddleware:
    """Re-encoda corpos JSON cp1252/latin-1 para UTF-8 antes do parsing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method", "GET").upper() not in _WRITE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
        if content_type and not content_type.startswith("application/json"):
            await self.app(scope, receive, send)
            return

        # Bufferiza o corpo (tamanho já limitado pelo BodyLimitMiddleware).
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # http.disconnect no meio do corpo: repassa ao app.
                await self.app(scope, _replay([message], receive), send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        if body:
            new_body = _to_utf8(body, _declared_charset(content_type))
            if new_body is not body:
                body = new_body
                scope = {
                    **scope,
                    "headers": [
                        (name, value)
                        for name, value in scope.get("headers", [])
                        if name != b"content-length"
                    ]
                    + [(b"content-length", str(len(body)).encode("ascii"))],
                }

        replay = _replay([{"type": "http.request", "body": body, "more_body": False}], receive)
        await self.app(scope, replay, send)


def _replay(messages: list[dict], receive):
    """Entrega as mensagens bufferizadas e depois delega ao receive original."""
    pending = list(messages)

    async def receiver():
        if pending:
            return pending.pop(0)
        return await receive()

    return receiver
