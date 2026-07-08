"""Resposta JSON com charset explícito.

O FastAPI emite `application/json` sem charset; clientes que seguem o RFC
assumem UTF-8, mas clientes legados (ex.: PowerShell 5.1 / .NET Framework)
caem para Latin-1 e corrompem acentos. Declarar o charset elimina a
ambiguidade para todos.
"""

from fastapi.responses import JSONResponse

JSON_UTF8 = "application/json; charset=utf-8"


class UTF8JSONResponse(JSONResponse):
    media_type = JSON_UTF8
