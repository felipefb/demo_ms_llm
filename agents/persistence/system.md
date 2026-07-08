# Agente 2 — Persistência (PostgreSQL)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Implementar a camada de persistência dos prompts e respostas.

1. Modele a tabela `interactions`: id (uuid pk), user_id (indexado), prompt (text),
   response (text, nullable), model (varchar), provider (varchar), status
   (enum: pending|completed|failed), error_detail (text nullable), latency_ms (int),
   prompt_tokens/completion_tokens (int nullable), created_at/updated_at (timestamptz).
   Índice composto (user_id, created_at DESC) para o histórico.
2. Configure SQLAlchemy 2.0 async (asyncpg) com pool configurável por env var e
   Alembic com a migration inicial.
3. Implemente o padrão Repository (app/repositories/interaction_repository.py) com:
   create_pending(), mark_completed(), mark_failed(), list_by_user() paginado
   (limit/offset ou cursor). O serviço grava o prompt ANTES de chamar o LLM
   (status=pending) e atualiza depois — assim nenhum prompt se perde se o LLM falhar.
4. Ligue o repositório real ao endpoint /v1/chat e ao GET /v1/conversations/{user_id}.
5. Atualize o docker-compose com serviço postgres:16 (volume nomeado, healthcheck)
   e faça a app aguardar o banco ficar saudável.
6. Testes: repositório com banco real via testcontainers (ou fixture com
   docker-compose) + testes do fluxo pending→completed e pending→failed.

CRITÉRIOS DE ACEITE: `docker compose up` sobe app+banco; migration roda
automaticamente (ou via comando documentado); um POST /v1/chat gera linha no banco
mesmo quando o LLM falha (status=failed); pytest verde.
