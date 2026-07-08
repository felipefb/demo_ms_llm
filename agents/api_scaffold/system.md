# Agente 1 — Scaffold do serviço e API REST

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Criar o esqueleto do micro-serviço e o endpoint principal.

1. Crie a estrutura de projeto conforme o Contexto Base (app/, tests/, docs/, migrations/),
   com pyproject.toml, .gitignore para Python, .env.example e configuração via
   pydantic-settings (app/core/config.py).
2. Implemente POST /v1/chat:
   - Request (Pydantic): user_id (str, obrigatório), prompt (str, obrigatório,
     1..4000 chars), opcionais: model (str), metadata (dict).
   - Response: id (uuid), user_id, prompt, response, model, tokens de uso se disponíveis,
     timestamp (UTC ISO-8601), latency_ms.
   - Validação com mensagens de erro claras (422 padronizado).
3. Implemente GET /health (liveness) e GET /ready (readiness — checa DB e conectividade
   de saída) e GET /v1/conversations/{user_id} (histórico paginado, será ligado ao banco
   pelo Agente 2 — deixe a interface do repositório pronta com implementação em memória).
4. Padronize TODAS as respostas de erro em um envelope único
   { "error": { "code", "message", "request_id" } } via exception handlers globais.
5. Adicione middleware de request_id (header X-Request-ID, gera uuid se ausente) e
   propague-o em logs e respostas.
6. Exponha OpenAPI/Swagger em /docs com descrições e exemplos nos schemas.
7. Crie testes de contrato do endpoint (payload válido, inválido, prompt vazio,
   prompt acima do limite) usando TestClient com o LLM mockado.

CRITÉRIOS DE ACEITE: `uvicorn app.main:app` sobe sem erros; /docs renderiza;
pytest verde; nenhuma chamada externa real nos testes.
