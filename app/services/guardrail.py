"""Guardrail de escopo temático: só o escopo do serviço é respondido.

O escopo do serviço é POSITIVO e configurável (GUARDRAIL_SCOPE; default:
indicadores econômico-financeiros, ex.: cotação do dólar). Prompts fora dele
são bloqueados por duas camadas complementares:

1. Pré-filtro determinístico (TopicGuardrail): regex por categoria de temas
   sensíveis conhecidos (política, religião por padrão) sobre o texto
   normalizado (minúsculas, sem acentos), avaliado ANTES do cache e do LLM —
   zero tokens gastos.
2. Escopo positivo semântico (build_system_prompt + is_out_of_scope_response):
   o system prompt declara o escopo e instrui o modelo a responder com a
   sentinela FORA_DO_ESCOPO para qualquer tema divergente que o pré-filtro
   não conheça (previsão do tempo, esportes, receitas...). O endpoint detecta
   a sentinela e converte em bloqueio — mesma resposta controlada.

Nas duas camadas a tentativa é persistida com status=blocked (auditável em
`GET /v1/conversations`), gera WARNING no log estruturado e incrementa a
métrica `guardrail_blocked_total{category}`.

Configuração: GUARDRAIL_BLOCKED_TOPICS (JSON categoria->regexes) customiza o
pré-filtro; GUARDRAIL_ENABLED=false o desliga. GUARDRAIL_SCOPE redefine o
escopo; GUARDRAIL_SCOPE_ENABLED=false desliga a camada semântica.
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


# Sentinela que o modelo devolve quando o prompt foge do escopo declarado.
SCOPE_SENTINEL = "FORA_DO_ESCOPO"


def build_system_prompt(settings: Settings) -> str:
    """System prompt efetivo: base + declaração de escopo com sentinela.

    A cláusula de escopo é a 2ª camada do guardrail: cobre temas divergentes
    imprevisíveis (tempo, esportes...) que o pré-filtro por regex não conhece.
    """
    prompt = settings.llm_system_prompt
    if settings.guardrail_scope_enabled:
        prompt += (
            f" ESCOPO DO SERVICO: {settings.guardrail_scope}. "
            "Se a mensagem do usuario estiver fora desse escopo (ex.: previsao "
            "do tempo, esportes, entretenimento, saude, conselhos pessoais, "
            "politica, religiao), NAO responda ao tema: devolva o campo "
            f'"resposta" contendo exatamente {SCOPE_SENTINEL}, sem mais nada.'
        )
    return prompt


def is_out_of_scope_response(text: str) -> bool:
    """Detecta a sentinela na resposta do modelo (em JSON ou texto cru)."""
    return SCOPE_SENTINEL.lower() in _normalize(text)


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
