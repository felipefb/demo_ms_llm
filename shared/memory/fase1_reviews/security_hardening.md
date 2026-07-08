# Parecer de Segurança — Scaffold Fase 1 (Rodada 1)

Revisor: Agente 4 — Segurança (security_hardening)
Escopo revisado: `app/` (config, errors, middleware, logging, schemas, api, services, deps), `.env.example`, `.gitignore`, testes.

## Veredito: AJUSTES

## Pontos positivos
- Envelope de erro (`app/core/errors.py`) não vaza stack trace: handler genérico retorna mensagem fixa "An unexpected error occurred." e loga a exceção só no servidor. `request_id` no envelope facilita correlação sem expor internals.
- `.env` e `.env.*` no `.gitignore` (com exceção correta `!.env.example`); `.env.example` só contém placeholders (`changeme`), nenhum segredo real.
- Logs do middleware registram apenas método, path, status e latência — nenhum body/prompt/API key logado; `request_id` injetado via filter.
- Limites de entrada nos campos: `prompt` max 4000, `user_id` max 128, paginação com `le=100`.
- Pontos de plug da Fase 4 estão bons: `create_app()` centraliza middlewares/handlers (auth e security headers entram como middleware sem refatoração), `app/api/deps.py` permite injetar dependência de API key, e `errors.py` já mapeia códigos 401/403/429 no envelope — rate limit e auth se encaixam direto.

## Ajustes necessários
1. **`extra="forbid"` ausente nos schemas** (`app/schemas/chat.py`): `ChatRequest`, `TokenUsage` etc. aceitam campos extras silenciosamente. Adicionar `extra="forbid"` no `model_config` de todos os modelos de entrada (no mínimo `ChatRequest`).
2. **`metadata: dict | None` sem limites** (`app/schemas/chat.py`): dict livre permite payloads arbitrariamente grandes/aninhados, burlando o limite de 4000 do prompt. Restringir (ex.: `dict[str, str]` com validador de nº de chaves e tamanho de valores) ou impor limite de tamanho serializado. Complementa, mas não substitui, o limite global de body (413) que a Fase 4 adicionará via middleware.
3. **Sem fail-fast de configuração** (`app/core/config.py`): todos os campos têm default (inclusive `database_url` com `postgres:postgres`), então o serviço sobe sem nenhuma key. Adicionar validação de inicialização (ex.: em `app_env != "dev"`, exigir keys obrigatórias e futura `API_KEY_HASH`) com mensagem clara. `extra="ignore"` está ok.
4. **`model` escolhido pelo cliente sem allowlist** (`ChatRequest.model` → `llm.generate(model=payload.model)`): usuário pode requisitar qualquer modelo arbitrário (custo/abuso quando o cliente real existir). Validar contra allowlist configurável ou remover o campo do contrato público.
5. **`GET /v1/conversations/{user_id}` sem qualquer autorização**: hoje qualquer chamador lê o histórico de qualquer usuário (IDOR). A Fase 4 adiciona API key global, mas registrar que a autorização por dono do recurso fica como limitação documentada em `docs/security.md`.
6. **`docs_url`/`redoc_url` fixos** (`app/main.py`): tornar condicionais a env (`app_env == "prod"` → desabilitar), conforme requisito da Fase 4 — deixar o toggle previsto em `Settings` já evita refatoração.
7. **`str(exc.detail)` no handler de HTTPException** (`errors.py`): se algum código interno levantar `HTTPException` com detail contendo informação interna, ela vaza no envelope. Baixo risco hoje; padronizar mensagens por status (usar o mapa de códigos também para a mensagem) ou revisar na Fase 4.

Itens 1–3 são bloqueantes para o aceite da fase; 4–7 podem ser absorvidos pela Fase 4, mas devem ficar registrados.

## Rodada 2

Veredito: APROVADO

Verificação item a item (código re-revisado em 2026-07-06; `pytest` verde, 29 passed):

1. **RESOLVIDO** — `ChatRequest` e `TokenUsage` com `extra="forbid"` (`app/schemas/chat.py`). Modelos de saída sem forbid, o que é aceitável (não são superfície de entrada).
2. **RESOLVIDO** — `metadata: dict[str, str] | None` com validador: máx. 20 chaves e 4096 bytes serializados (UTF-8), constantes nomeadas e documentadas no schema.
3. **RESOLVIDO** — `Settings.fail_fast_config` (`app/core/config.py`): em env != dev/test ou com `REQUIRE_LLM_KEYS=true`, exige `OPENROUTER_API_KEY` e `GEMINI_API_KEY`; `"changeme"` conta como ausente; mensagem clara apontando o `.env.example`. Coberto por `tests/test_config.py`.
4. **RESOLVIDO** — `ALLOWED_MODELS` (allowlist configurável) aplicada em `_validate_model` (`app/api/v1/chat.py`) com default seguro: sem allowlist, cliente não pode sobrescrever o modelo (422 `model_not_allowed`).
5. **CONFIRMADO (sem regressão)** — `GET /v1/conversations/{user_id}` permanece como estava (IDOR conhecido); mitigação/documentação segue delegada à Fase 4 (`docs/security.md` + API key).
6. **RESOLVIDO** — `docs_url`/`redoc_url`/`openapi_url` condicionais a `settings.docs_enabled`; `app_env == "prod"` força desabilitado (validator + property), com teste.
7. **RESOLVIDO** — handler de `HTTPException` usa mapa de mensagens padronizadas por status e nunca expõe `exc.detail` (loga detail apenas para 5xx no servidor).

Sem pendências novas para a Fase 1. Registrar para a Fase 4: item 5 (autorização por dono do recurso em `docs/security.md`), limite global de body (413) via middleware, e `API_KEY_HASH` no fail-fast quando a auth entrar.
