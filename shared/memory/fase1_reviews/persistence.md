# Revisão Fase 1 — Persistência (PostgreSQL)

Veredito: AJUSTES

O scaffold está bem organizado (Protocol + injeção via deps.py substituível por
`app.dependency_overrides`, métodos async, paginação limit/offset com total), mas a
interface atual NÃO permite plugar a Fase 2 sem refatorar os endpoints. Ajustes:

1. **Interface `save()` incompatível com o fluxo pending→completed/failed**
   `ConversationRepository` (app/repositories/conversations.py) expõe apenas
   `save(record)`, e o endpoint `/v1/chat` (app/api/v1/chat.py) grava SOMENTE APÓS
   o LLM responder. O requisito da Fase 2 é gravar ANTES (status=pending) e
   atualizar depois — isso exigirá reescrever o endpoint. Trocar a interface já
   agora para:
   - `create_pending(user_id, prompt, model) -> ConversationRecord`
   - `mark_completed(id, response, model, provider, latency_ms, usage) -> ConversationRecord`
   - `mark_failed(id, error_detail, latency_ms) -> ConversationRecord`
   - `list_by_user(user_id, limit, offset) -> tuple[list[ConversationRecord], int]` (ok como está)
   E ajustar o endpoint para: create_pending → chamar LLM em try/except →
   mark_completed / mark_failed (re-raise). A InMemory implementa o mesmo contrato.

2. **`ConversationRecord` sem os campos que a tabela `interactions` terá**
   Faltam: `status` (pending|completed|failed), `provider`, `error_detail`,
   `latency_ms`, `prompt_tokens`/`completion_tokens` como campos explícitos (hoje
   escondidos num dict `usage`), `created_at`/`updated_at` (hoje um único
   `timestamp`). Além disso `response: str` deve ser `str | None` — um registro
   pending/failed não tem resposta.

3. **Schemas de resposta sem status/provider**
   `ChatResponse` e `ConversationItem` (app/schemas/chat.py) não expõem `status`
   nem `provider`; `ConversationItem` exige `response: str` não-nulo, o que
   quebrará ao listar interações failed/pending persistidas. Tornar `response`
   opcional e adicionar `status` (e `provider` no ChatResponse).

4. **`latency_ms` calculado só no endpoint e descartado**
   É medido em chat.py mas não entra no record — na Fase 2 precisa ser persistido
   (coluna `latency_ms int`). Passar via `mark_completed`/`mark_failed`.

5. **deps.py sem gancho de ciclo de vida (menor)**
   Singletons de módulo funcionam para in-memory, mas o engine async do SQLAlchemy
   precisa de init/dispose no lifespan do FastAPI. Sugestão: manter `get_repository()`
   como está, mas prever que a Fase 2 troque a factory no lifespan (nenhuma mudança
   de assinatura nos endpoints é necessária para isso — apenas registrar aqui).

Pontos positivos (manter): Protocol assíncrono, DI substituível, paginação com
limit/offset validados (ge/le), `id` uuid + `user_id` str compatíveis com o modelo
proposto, `metadata`/`usage` já pensados para analytics.

## Rodada 2

Veredito: APROVADO

Verificação item a item da Rodada 1:

1. Interface pending→completed/failed — RESOLVIDO. `ConversationRepository`
   (app/repositories/conversations.py) agora expõe `create_pending()`,
   `mark_completed()`, `mark_failed()` e `list_by_user()`; a InMemory implementa
   o contrato completo e levanta `RecordNotFoundError` para id inexistente. O
   endpoint /v1/chat grava o prompt ANTES do LLM, faz try/except e persiste
   `failed` com re-raise (503 `llm_unavailable`).
2. `ConversationRecord` — RESOLVIDO. Campos `status`, `provider`, `error_detail`,
   `latency_ms`, `prompt_tokens`/`completion_tokens`, `created_at`/`updated_at`
   explícitos; `response: str | None`.
3. Schemas — RESOLVIDO. `ChatResponse` e `ConversationItem` expõem `status` e
   `provider`; `response` é opcional em ambos.
4. `latency_ms` persistido — RESOLVIDO. Medido no endpoint e passado a
   `mark_completed`/`mark_failed`; testes confirmam que fica no record.
5. Ciclo de vida em deps.py — RESOLVIDO. Recursos criados no lifespan
   (app/main.py) e guardados em `app.state`; deps.py resolve via
   `request.app.state`, mantendo override em testes. Pronto para a Fase 2 trocar
   por engine async com dispose no shutdown.

Testes: 29 passed (inclui pending→completed, pending→failed com prompt
preservado, histórico com registros failed e paginação com status/provider).

Observação não bloqueante: `latency_ms` no record é `float`; a coluna proposta
na Fase 2 é `int` — decidir arredondamento na migration (não impede aprovação).
