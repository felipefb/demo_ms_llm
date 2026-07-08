"""Normalização da resposta do LLM em um esquema estruturado estável.

Os providers instruem o modelo a responder JSON puro no esquema:

    {
      "resposta": "frase direta com a informacao pedida",
      "dados":    [{"indicador": "...", "valor": "...", "fonte": "..."}],
      "contexto": "paragrafo opcional (so no modo detailed)",
      "fontes":   ["url1", "url2"]
    }

`resposta` é obrigatória; os demais campos são opcionais. `dados` é a
matéria-prima para normalizações downstream (uma linha por indicador —
vira tabela direto). O parser é tolerante: aceita cercas de código
(```json ... ```), ignora chaves desconhecidas e, se o modelo não
devolver JSON válido, degrada com segurança usando o texto cru como
`resposta` — o cliente nunca recebe erro por formatação do modelo.
"""

import json
import re
from dataclasses import dataclass, field

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@dataclass
class StructuredAnswer:
    resposta: str
    dados: list[dict[str, str]] = field(default_factory=list)
    contexto: str | None = None
    fontes: list[str] = field(default_factory=list)
    normalizada: bool = True  # False quando caímos no fallback de texto cru


def _clean_item(item: object) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    cleaned = {str(k): str(v) for k, v in item.items() if v is not None}
    return cleaned or None


def parse_structured_answer(raw_text: str | None) -> StructuredAnswer:
    """Extrai o esquema estruturado do texto do modelo (nunca levanta exceção)."""
    text = (raw_text or "").strip()
    candidate = _FENCE_RE.sub("", text).strip()
    # Alguns modelos prefixam frases antes do JSON; tenta do primeiro '{'.
    if not candidate.startswith("{"):
        brace = candidate.find("{")
        candidate = candidate[brace:] if brace >= 0 else candidate
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        return StructuredAnswer(resposta=text, normalizada=False)
    if not isinstance(data, dict) or not str(data.get("resposta") or "").strip():
        return StructuredAnswer(resposta=text, normalizada=False)

    dados = [c for c in (_clean_item(i) for i in data.get("dados") or []) if c]
    fontes = [str(f) for f in data.get("fontes") or [] if f]
    contexto = data.get("contexto")
    return StructuredAnswer(
        resposta=str(data["resposta"]).strip(),
        dados=dados,
        contexto=str(contexto).strip() if contexto else None,
        fontes=fontes,
    )
