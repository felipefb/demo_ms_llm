# Parecer Fase 1 — Integração LLM (Agente 3)

Revisor: llm_integration | Rodada 1 | Escopo: `app/services/llm.py`, `app/api/v1/chat.py`, `app/api/health.py`, `app/main.py`, `app/api/deps.py`, `app/core/config.py`

## Veredito: AJUSTES

O scaffold está no caminho certo: `LLMClient` é um `Protocol` assíncrono e mockável, o endpoint depende dele via `Depends(get_llm_client)` (overridável em testes), `/ready` já reserva o check `llm_egress`, o envelope de erro (`AppError`, 503) existe e o `Settings` já traz OPENROUTER_*/GEMINI_*/`llm_timeout_seconds`. Porém há 4 pontos que bloqueiam a Fase 3 se não forem corrigidos:

## Ajustes

1. **`LLMResult` sem campo `provider`** (`app/services/llm.py`). O contrato exigido é `text, model, provider, usage`. Adicionar `provider: str` (ex.: `"echo"`, `"openrouter"`, `"gemini"`) ao dataclass e propagar até `ChatResponse`/registro persistido — sem isso não dá para registrar qual provider atendeu no fallback sem quebrar o contrato depois.

2. **Não existe `lifespan` em `create_app()`** (`app/main.py`). A Fase 3 precisa criar um único `httpx.AsyncClient` (connection pool) no startup e fechá-lo no shutdown. Adicionar `lifespan=` no `FastAPI(...)` (mesmo que vazio/no-op agora) e um lugar canônico para recursos compartilhados (ex.: `app.state.http_client`), para que os providers e o check `llm_egress` do `/ready` o consumam.

3. **Singletons de módulo em `app/api/deps.py`** (`_llm_client = EchoLLMClient()` criado no import). Instanciação no import impede injetar o client httpx criado no lifespan. Trocar por providers que resolvam a partir de `request.app.state` (ou factory chamada no lifespan), mantendo `EchoLLMClient` como default quando não há API keys.

4. **Fluxo do `/v1/chat` não persiste falhas** (`app/api/v1/chat.py`). O registro só é criado após `llm.generate()` ter sucesso; o requisito é "o prompt nunca se perde" com `status=failed` quando ambos os providers caem. Preparar o contrato: (a) campo `status` no record/`new_record` (junto com persistence); (b) estrutura do endpoint que permita capturar `LLMUnavailableError` (a definir em Fase 3 como subclasse de `AppError` com 503/`llm_unavailable`), persistir a interação como failed e re-levantar. Pode ficar como TODO explícito, mas o campo `status` deve nascer no schema do banco na Fase 2.

## Observações (não bloqueiam)

- `latency_ms` medido no endpoint é aceitável, mas na Fase 3 a latência por provider virá dentro do `LLMResult`/logs — manter os dois é ok.
- `usage` como campos achatados (`prompt_tokens/...`) no dataclass é aceitável; se preferir aderência literal ao contrato, agrupar num sub-objeto `usage`.
- `gemini_model: "gemini-1.5-flash"` — considerar `gemini-2.0-flash` (mais novo, free tier); é só default de env, não bloqueia.
- `/ready` com contrato estável e TODO para `llm_egress` está correto; o check deve usar o client do lifespan com timeout curto (~2s), não o timeout de 30s.

## Rodada 2

Veredito: APROVADO

Verificação item a item do parecer da Rodada 1:

1. **`LLMResult.provider`** — RESOLVIDO. `app/services/llm.py` tem `provider: str` no dataclass; `EchoLLMClient` preenche `provider="echo"`; propagado até `ChatResponse.provider` e persistido via `mark_completed(..., provider=...)`.
2. **`lifespan` em `create_app()`** — RESOLVIDO. `app/main.py` define `lifespan` que cria um único `httpx.AsyncClient` em `app.state.http_client` (timeout de `settings.llm_timeout_seconds`) e o fecha no shutdown; `FastAPI(..., lifespan=lifespan)`.
3. **Singletons em `deps.py`** — RESOLVIDO. Sem instanciação no import; `get_repository`/`get_llm_client` resolvem via `request.app.state`, mantendo override em testes.
4. **Persistência de falhas no `/v1/chat`** — RESOLVIDO. Fluxo `create_pending` ANTES do LLM; em exceção, `mark_failed` (com `error_detail` e `latency_ms`) e re-raise como `AppError` 503 `llm_unavailable`. Repositório tem `status pending/completed/failed`. Coberto por testes (`test_llm_failure_persists_failed_record_and_returns_503`, `test_conversation_history_includes_failed_records`).

Suite: `pytest -q` → 29 passed, sem rede.

Observação não bloqueante (herdada): defaults de modelo Gemini e timeout curto no check `llm_egress` do `/ready` ficam para a Fase 3.
