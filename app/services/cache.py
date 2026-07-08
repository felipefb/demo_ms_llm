"""Cache in-memory de respostas do LLM com TTL.

Performance com custo controlado: prompts idênticos (mesmo texto + modo)
dentro da janela de TTL reutilizam a resposta já gerada — latência de
milissegundos e zero tokens gastos. O TTL curto (default 60s) mantém a
atualidade de dados de mercado; LLM_CACHE_TTL_SECONDS=0 desliga.

Single-réplica (mesma limitação documentada do rate limit); em produção
o equivalente é o ElastiCache/Redis previsto em docs/architecture.
"""

import hashlib
import time
from dataclasses import dataclass

from app.services.llm import LLMResult

_MAX_ENTRIES = 1024


@dataclass
class CachedAnswer:
    result: LLMResult
    expires_at: float


class ResponseCache:
    def __init__(self, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._entries: dict[str, CachedAnswer] = {}

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    @staticmethod
    def _key(prompt: str, mode: str, model: str | None) -> str:
        raw = f"{mode}\x00{model or ''}\x00{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, prompt: str, mode: str, model: str | None) -> LLMResult | None:
        if not self.enabled:
            return None
        entry = self._entries.get(self._key(prompt, mode, model))
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            self._entries.pop(self._key(prompt, mode, model), None)
            return None
        return entry.result

    def put(self, prompt: str, mode: str, model: str | None, result: LLMResult) -> None:
        if not self.enabled:
            return
        if len(self._entries) >= _MAX_ENTRIES:
            # Descarta o mais antigo (dict preserva ordem de inserção).
            self._entries.pop(next(iter(self._entries)), None)
        self._entries[self._key(prompt, mode, model)] = CachedAnswer(
            result=result, expires_at=time.monotonic() + self._ttl
        )
