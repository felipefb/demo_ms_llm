"""Guardrail de escopo temático: bloqueia prompts sobre temas divergentes.

O serviço responde a temas do escopo (ex.: indicadores econômico-financeiros,
como a cotação do dólar). Temas divergentes — política e religião por padrão —
são bloqueados por duas camadas complementares:

1. Pré-filtro determinístico (este módulo): regex por categoria sobre o texto
   normalizado (minúsculas, sem acentos), avaliado ANTES do cache e do LLM —
   zero tokens gastos. A tentativa é persistida com status=blocked (auditável
   em `GET /v1/conversations`), gera WARNING no log estruturado e incrementa a
   métrica `guardrail_blocked_total{category}`.
2. System prompt (LLM_SYSTEM_PROMPT) instrui o modelo a recusar temas fora do
   escopo que o pré-filtro não capturar — keywords têm cobertura parcial por
   construção (limitação documentada em docs/security.md).

Categorias customizáveis via GUARDRAIL_BLOCKED_TOPICS (JSON categoria->regexes);
GUARDRAIL_ENABLED=false desliga o pré-filtro (a camada do system prompt fica).
"""

import re
import unicodedata
from dataclasses import dataclass

from app.core.config import Settings

# Expressões do domínio econômico-financeiro que contêm termos bloqueados:
# são removidas do texto antes da checagem para não gerar falso positivo
# (ex.: "política monetária do Banco Central" está dentro do escopo).
DEFAULT_SCOPE_EXCEPTIONS: list[str] = [
    r"\bpolitic[ao]s?\s+(?:monetari\w+|fiscal|fiscais|cambial|cambiais"
    r"|economic\w+|tributari\w+|de\s+\w+)",
]

# Regexes avaliadas sobre o texto normalizado (minúsculas, sem acentos).
DEFAULT_BLOCKED_TOPICS: dict[str, list[str]] = {
    "politica": [
        r"\bpolitic\w*",  # política(o/os), politics, political, politician
        r"\beleic(?:ao|oes)\b",
        r"\beleitora\w*",
        r"\belection\w*",
        r"\bcandidat\w*",
        r"\bpartidari\w*",
        r"\bvotar\b",
    ],
    "religiao": [
        r"\breligi\w*",  # religião(oso/ões), religion, religious
        r"\bdeus(?:es)?\b",
        r"\bgod\b",
        r"\bbiblia\b",
        r"\bbiblic\w*",
        r"\bigrejas?\b",
        r"\bchurch(?:es)?\b",
        r"\bcatolic\w*",
        r"\bevangelic\w*",
        r"\bislamic\w*|\bmuculman\w*|\balcorao\b",
        r"\bjudais\w*|\bbudis\w*|\bespiritis\w*",
        r"\bate(?:u|ia|ismo)\b",
    ],
}


def _normalize(text: str) -> str:
    """Minúsculas + remoção de acentos ("Religião" -> "religiao")."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass(frozen=True)
class GuardrailMatch:
    category: str
    term: str


class TopicGuardrail:
    """Detecta temas bloqueados no prompt; devolve a categoria ou None."""

    def __init__(
        self,
        topics: dict[str, list[str]] | None = None,
        exceptions: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._topics = {
            category: [re.compile(p) for p in patterns]
            for category, patterns in (topics or DEFAULT_BLOCKED_TOPICS).items()
        }
        self._exceptions = [
            re.compile(p) for p in (DEFAULT_SCOPE_EXCEPTIONS if exceptions is None else exceptions)
        ]

    @classmethod
    def from_settings(cls, settings: Settings) -> "TopicGuardrail":
        return cls(
            topics=settings.guardrail_blocked_topics or None,
            enabled=settings.guardrail_enabled,
        )

    def check(self, prompt: str) -> GuardrailMatch | None:
        if not self._enabled:
            return None
        text = _normalize(prompt)
        for exception in self._exceptions:
            text = exception.sub(" ", text)
        for category, patterns in self._topics.items():
            for pattern in patterns:
                found = pattern.search(text)
                if found:
                    return GuardrailMatch(category=category, term=found.group(0))
        return None
