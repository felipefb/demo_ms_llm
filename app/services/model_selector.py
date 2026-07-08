"""Seleção automática de modelos por provider (OpenRouter + Gemini).

Consulta periodicamente o catálogo público de cada provider e escolhe o
melhor modelo para cada modo de resposta, sem exigir configuração manual.

Heurística de ranking (documentada aqui de propósito — é o contrato):

1. Elegibilidade
   - O modelo precisa suportar chat/generateContent.
   - Limite de saída (max_completion_tokens / outputTokenLimit) >= teto de
     tokens do modo (LLM_DIRECT_MAX_TOKENS / LLM_DETAILED_MAX_TOKENS).
   - Contexto (context_length / inputTokenLimit) >= 8k — margem folgada para
     prompts de até 4000 chars (~1000 tokens) + system prompt + saída.
   - Modelos experimentais/preview (sufixos "exp"/"preview"/"beta" no id)
     são excluídos, a menos que não exista nenhum modelo estável elegível.

2. Preço
   - OpenRouter: modelos free (pricing.prompt == pricing.completion == 0)
     vêm primeiro; entre pagos, menor custo por token vence.
   - Gemini: a API key free tier já limita o catálogo; preferimos a família
     "flash" (free tier generosa e barata em produção).

3. Qualidade por modo
   - direct  -> resposta curta/objetiva: o MENOR modelo elegível do tier
     mais barato (menos parâmetros no id, ex. 3b < 9b; Gemini: "flash-lite").
     Empate: versão mais recente.
   - detailed -> resposta com contexto: o modelo MAIS capaz dentro do tier
     mais barato (mais parâmetros, ex. 72b; Gemini: "flash" não-lite).
     Empate: versão mais recente (maior número de versão no id/nome).

Cache com TTL (LLM_MODEL_REFRESH_SECONDS, default 3600): a seleção acontece
no startup e é revista de forma lazy no primeiro request após expirar, com
`asyncio.Lock` para evitar estouro de consultas concorrentes. Falha na
consulta do catálogo NUNCA derruba o request: mantém a última seleção boa
ou, sem seleção alguma, devolve None (o provider usa o default do env).
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.core.metrics import set_selected_model

logger = logging.getLogger("app.llm")

MODES = ("direct", "detailed")
MIN_CONTEXT_TOKENS = 8192
# Piso de qualidade do modo direct: modelos minúsculos (<3B) são baratos mas
# instáveis/fracos demais; só entram se não houver alternativa.
MIN_DIRECT_PARAM_B = 3.0
_UNSTABLE_RE = re.compile(r"(exp|preview|beta)", re.IGNORECASE)
# Modelos que não são chat de texto puro (imagem/áudio/vídeo/embeddings) ou que
# gastam tokens em raciocínio interno ("thinking") — inadequados para o chat.
_NON_TEXT_RE = re.compile(
    r"(image|imagen|vision-only|veo|tts|audio|video|whisper|embed|aqa|live|moderation)",
    re.IGNORECASE,
)
_THINKING_RE = re.compile(r"(thinking|reasoner|reasoning)", re.IGNORECASE)
# Tamanho em parâmetros no id, ex.: "llama-3.2-3b" -> 3.0, "qwen-72b" -> 72.0.
_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
# Números de versão no id/nome, ex.: "llama-3.3" -> 3.3, "gemini-2.5-flash" -> 2.5.
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class CatalogModel:
    """Visão normalizada de um modelo de catálogo, provider-agnóstica."""

    model_id: str
    free: bool
    price: float  # custo prompt+completion por token (0.0 quando free)
    context_tokens: int
    output_tokens: int
    stable: bool
    param_b: float | None  # bilhões de parâmetros inferidos do id (se houver)
    version: float  # maior número de versão presente no id/nome


def _parse_param_b(model_id: str) -> float | None:
    match = _PARAM_RE.search(model_id)
    return float(match.group(1)) if match else None


def _parse_version(model_id: str) -> float:
    # Ignora o tamanho em parâmetros ("70b") para não confundir com versão.
    cleaned = _PARAM_RE.sub("", model_id)
    numbers = [float(n) for n in _VERSION_RE.findall(cleaned)]
    return max(numbers) if numbers else 0.0


def parse_openrouter_catalog(payload: dict) -> list[CatalogModel]:
    models: list[CatalogModel] = []
    for item in payload.get("data") or []:
        model_id = item.get("id")
        if not model_id:
            continue
        pricing = item.get("pricing") or {}
        try:
            prompt_price = float(pricing.get("prompt") or 0)
            completion_price = float(pricing.get("completion") or 0)
        except (TypeError, ValueError):
            continue
        # Só chat de texto: descarta modelos de imagem/áudio/embeddings pela
        # modalidade declarada no catálogo e, na ausência dela, pelo nome.
        arch = item.get("architecture") or {}
        modality = str(arch.get("modality") or "")
        output_modalities = arch.get("output_modalities") or []
        if modality and not modality.endswith("->text"):
            continue
        if output_modalities and output_modalities != ["text"]:
            continue
        if _NON_TEXT_RE.search(model_id):
            continue
        context = int(item.get("context_length") or 0)
        top = item.get("top_provider") or {}
        output = int(top.get("max_completion_tokens") or context)
        models.append(
            CatalogModel(
                model_id=model_id,
                free=prompt_price == 0 and completion_price == 0,
                price=prompt_price + completion_price,
                context_tokens=context,
                output_tokens=output,
                stable=not _UNSTABLE_RE.search(model_id),
                param_b=_parse_param_b(model_id),
                version=_parse_version(model_id),
            )
        )
    return models


def parse_gemini_catalog(payload: dict) -> list[CatalogModel]:
    models: list[CatalogModel] = []
    for item in payload.get("models") or []:
        name = (item.get("name") or "").removeprefix("models/")
        methods = item.get("supportedGenerationMethods") or []
        if not name or "generateContent" not in methods:
            continue
        if "gemini" not in name.lower():
            continue  # embeddings/aqa/imagen etc.
        if _NON_TEXT_RE.search(name):
            continue  # variantes de imagem/áudio/vídeo respondem 400 para texto
        models.append(
            CatalogModel(
                model_id=name,
                free=True,  # free tier: o preço é modelado pela família (flash)
                price=0.0,
                context_tokens=int(item.get("inputTokenLimit") or 0),
                output_tokens=int(item.get("outputTokenLimit") or 0),
                stable=not _UNSTABLE_RE.search(name),
                param_b=None,
                version=_parse_version(name),
            )
        )
    return models


def _eligible(models: list[CatalogModel], max_output_tokens: int) -> list[CatalogModel]:
    pool = [
        m
        for m in models
        if m.context_tokens >= MIN_CONTEXT_TOKENS
        and (max_output_tokens <= 0 or m.output_tokens >= max_output_tokens)
    ]
    stable = [m for m in pool if m.stable]
    return stable or pool  # instáveis só quando não há alternativa


def select_openrouter_model(
    models: list[CatalogModel], mode: str, max_tokens: int, grounded: bool = False
) -> str | None:
    """Aplica a heurística documentada no módulo ao catálogo do OpenRouter."""
    pool = _eligible(models, max_tokens)
    if not pool:
        return None
    free = [m for m in pool if m.free]
    if free:
        pool = free
    else:
        cheapest = min(m.price for m in pool)
        pool = [m for m in pool if m.price == cheapest]
    if mode == "detailed" or grounded:
        # detailed sempre; e no direct com busca web a síntese de fontes exige
        # o modelo mais capaz do tier free (na demo, modelos médios alucinaram
        # valores e produziram texto corrompido) — no free tier o custo é igual.
        best = max(pool, key=lambda m: (m.param_b or 0.0, m.version, m.model_id))
    else:  # direct sem grounding: menor modelo com piso de qualidade
        solid = [
            m
            for m in pool
            if (m.param_b is None or m.param_b >= MIN_DIRECT_PARAM_B)
            and not _THINKING_RE.search(m.model_id)
        ]
        if solid:
            pool = solid
        best = min(
            pool,
            key=lambda m: (m.param_b if m.param_b is not None else float("inf"), -m.version),
        )
    return best.model_id


def select_gemini_model(
    models: list[CatalogModel], mode: str, max_tokens: int, grounded: bool = False
) -> str | None:
    """Aplica a heurística documentada no módulo ao catálogo do Gemini."""
    pool = _eligible(models, max_tokens)
    if not pool:
        return None
    flash = [m for m in pool if "flash" in m.model_id.lower()]
    if flash:
        pool = flash
    if mode == "direct" and not grounded:
        # Sem grounding o lite basta; com busca web a sintese pede o flash cheio.
        lite = [m for m in pool if "lite" in m.model_id.lower()]
        if lite:
            pool = lite
    elif mode == "direct" and grounded:
        non_lite = [m for m in pool if "lite" not in m.model_id.lower()]
        if non_lite:
            pool = non_lite
    else:
        non_lite = [m for m in pool if "lite" not in m.model_id.lower()]
        if non_lite:
            pool = non_lite
    return max(pool, key=lambda m: (m.version, m.model_id)).model_id


class ModelSelector:
    """Cache de seleção automática de modelos com refresh por TTL.

    `get_model(provider, mode)` nunca levanta exceção: devolve a última
    seleção boa ou None (caller usa o default do env como fallback final).
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        openrouter_api_key: str = "",
        gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        gemini_api_key: str = "",
        direct_max_tokens: int = 256,
        detailed_max_tokens: int = 1024,
        refresh_seconds: float = 3600.0,
        catalog_timeout_seconds: float = 2.0,
        web_search: bool = False,
    ):
        self._client = http_client
        self._openrouter_base_url = openrouter_base_url.rstrip("/")
        self._openrouter_api_key = openrouter_api_key
        self._gemini_base_url = gemini_base_url.rstrip("/")
        self._gemini_api_key = gemini_api_key
        self._max_tokens = {"direct": direct_max_tokens, "detailed": detailed_max_tokens}
        self._refresh_seconds = refresh_seconds
        self._timeout = catalog_timeout_seconds
        self._grounded = web_search
        self._lock = asyncio.Lock()
        self._selection: dict[tuple[str, str], str] = {}
        self._fetched_at: dict[str, float] = {}
        # Auto-cura: modelos que falharam com erro permanente (400/404) ficam
        # fora das próximas seleções desta instância.
        self._denylist: set[tuple[str, str]] = set()

    def mark_bad(self, provider: str, model_id: str) -> None:
        """Remove um modelo que falhou de forma permanente e força re-seleção."""
        if (provider, model_id) in self._denylist:
            return
        self._denylist.add((provider, model_id))
        self._fetched_at.pop(provider, None)  # força refresh no próximo request
        logger.warning(
            "model_selector provider=%s model=%s denylisted after permanent error",
            provider,
            model_id,
        )

    def _expired(self, provider: str) -> bool:
        fetched = self._fetched_at.get(provider)
        return fetched is None or (time.monotonic() - fetched) >= self._refresh_seconds

    async def _fetch_catalog(self, provider: str) -> list[CatalogModel]:
        if provider == "openrouter":
            headers = {}
            if self._openrouter_api_key:
                headers["Authorization"] = f"Bearer {self._openrouter_api_key}"
            response = await self._client.get(
                f"{self._openrouter_base_url}/models", headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            return parse_openrouter_catalog(response.json())
        response = await self._client.get(
            f"{self._gemini_base_url}/models",
            # Header (não query param): a key nunca aparece em URLs/logs.
            headers={"x-goog-api-key": self._gemini_api_key},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return parse_gemini_catalog(response.json())

    async def _refresh(self, provider: str) -> None:
        """Reconsulta o catálogo; em falha, mantém a última seleção boa."""
        try:
            catalog = await self._fetch_catalog(provider)
        except Exception as exc:  # noqa: BLE001 — nunca derrubar o request
            # Reagenda para o próximo TTL: evita martelar um catálogo fora do ar.
            self._fetched_at[provider] = time.monotonic()
            logger.warning(
                "model_selector provider=%s catalog fetch failed (%s); keeping last selection",
                provider,
                type(exc).__name__,
            )
            return
        catalog = [m for m in catalog if (provider, m.model_id) not in self._denylist]
        select = select_openrouter_model if provider == "openrouter" else select_gemini_model
        for mode in MODES:
            chosen = select(catalog, mode, self._max_tokens[mode], grounded=self._grounded)
            if chosen is None:
                logger.warning(
                    "model_selector provider=%s mode=%s: no eligible model in catalog",
                    provider,
                    mode,
                )
                continue
            previous = self._selection.get((provider, mode))
            self._selection[(provider, mode)] = chosen
            set_selected_model(provider, mode, chosen)
            if chosen != previous:
                logger.info(
                    "model_selector provider=%s mode=%s model=%s reason=%s",
                    provider,
                    mode,
                    chosen,
                    "cheapest eligible, smallest for direct / most capable for detailed",
                )
        self._fetched_at[provider] = time.monotonic()

    async def refresh_all(self, providers: tuple[str, ...] = ("openrouter", "gemini")) -> None:
        """Seleção inicial (startup). Best-effort: nunca levanta exceção."""
        async with self._lock:
            for provider in providers:
                await self._refresh(provider)

    async def get_model(self, provider: str, mode: str) -> str | None:
        if self._expired(provider):
            async with self._lock:
                if self._expired(provider):  # double-check após esperar o lock
                    await self._refresh(provider)
        return self._selection.get((provider, mode))
