# Parecer de Segurança — Fase 2, Rodada 1

Revisor: security_hardening (validador). Escopo: providers.py, resilience.py,
postgres.py, database.py, migrations/, docker-compose.yml, Dockerfile,
config.py, .env.example, alembic.ini, .gitignore.

## Veredito: APROVADO

## Checklist verificada

1. **API keys nunca em URL/logs/exceções — OK.**
   - OpenRouter: key só em header `Authorization: Bearer` (providers.py:71).
   - Gemini: key em header `x-goog-api-key`, nunca query param (providers.py:133),
     com comentário explícito.
   - Exceções `LLMProviderError` contêm apenas `[provider] HTTP <status> from
     upstream`, `request timed out` ou `transport error: <TypeName>` — sem key,
     sem URL, sem corpo (providers.py:37-43, 78-83).
   - Logs em resilience.py registram provider/model/latency/tokens e `str(exc)`
     das exceções acima — sem dados sensíveis.

2. **Corpos de erro upstream não vazam ao cliente — OK.**
   - `_classify_http_error` nunca inclui o body da resposta.
   - Falha total vira `LLMUnavailableError` (503, mensagem fixa genérica).
   - `error_detail` persistido no banco (`chat.py:70`, `{type}: {exc}`) usa a
     mensagem sanitizada das exceções do provider; e fica no DB, não na resposta.

3. **Credenciais do compose / DATABASE_URL — OK para dev.**
   - `postgres:postgres` em docker-compose.yml, alembic.ini, config.py default e
     .env.example são placeholders de dev consistentes; nenhum segredo real.
   - Fail-fast em config.py exige keys reais fora de dev/test (rejeita
     "changeme").

4. **SQL só via ORM/bound params — OK.**
   - postgres.py usa exclusivamente `session.get`, `select(...).where(...)` com
     expressões SQLAlchemy; zero interpolação de strings em SQL.
   - Único `sa.text()` é `"created_at DESC"` na migration (literal fixo de
     índice, não input de usuário) — aceitável.

5. **docker-compose portas — OK com ressalva (nota N1).**

6. **.env.example — OK.** Só placeholders (`changeme`); `.env`/`.env.*` no
   .gitignore (linhas 15-16).

7. **Migrations — OK.** Apenas DDL; nenhum dado, seed ou credencial. env.py lê
   DATABASE_URL do ambiente sem logá-la.

## Notas não bloqueantes (para Rodada 2 / packaging)

- **N1 (baixa):** `ports: "5432:5432"` expõe o Postgres ao host. Conveniente em
  dev, mas desnecessário para o app (rede interna do compose basta). Sugestão:
  `127.0.0.1:5432:5432` ou remover o mapeamento; ao menos documentar no README.
- **N2 (baixa):** Dockerfile roda como root (baseline declarado; Agente 7 deve
  incluir usuário não-root no multi-stage). Registrar como pendência.
- **N3 (info):** senha do Postgres no compose poderia vir de
  `${POSTGRES_PASSWORD:-postgres}` para paridade com as keys de LLM; opcional
  em dev.
