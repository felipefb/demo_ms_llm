# Parecer — security_hardening sobre a entrega de app_observability (Fase 3, Rodada 1)

Revisor: agente de segurança (validação cruzada). Data: 2026-07-06.
Escopo revisado: `app/core/middleware.py`, `app/core/logging.py`, `app/core/metrics.py`,
`app/core/tracing.py`, `app/services/resilience.py` (instrumentação), `app/main.py`
(ordem dos middlewares), `docker-compose.yml` (perfil observability), `docs/observability.md`.

## Veredito: AJUSTES

Entrega de alta qualidade — um único ajuste objetivo bloqueia a aprovação (item 1);
os demais são recomendações menores.

## Verificações que PASSARAM

1. **Logs não vazam dados sensíveis.** O access log (`app.access`, evento
   `http_request`) registra apenas `body_bytes` e `body_sha256` — nunca o corpo do
   prompt/response. Nenhum header é logado (portanto `X-API-Key` não aparece).
   `logging.py` não injeta headers; `uvicorn.access` desabilitado (evita o access
   log em texto plano do uvicorn). OK.
2. **500 do middleware não vaza internals.** Em exceção não tratada com resposta
   ainda não iniciada, o middleware emite `{"error":{"code":"internal_error",
   "message":"Internal server error."}}` — sem stack trace, sem str(exc). O
   traceback vai apenas para o log estruturado (`exc_info`), não para o cliente. OK.
3. **Ordem dos middlewares preservada.** `main.py`: RequestId (outermost) → CORS →
   SecurityHeaders → BodyLimit → ApiKeyAuth → RateLimit. Auth, rate limit e body
   limit continuam na cadeia e não foram contornados pela reescrita ASGI; o
   `receive_wrapper` apenas observa os chunks (hash/tamanho), não consome nem
   altera o body. OK.
4. **/metrics não expõe dados sensíveis.** Registry dedicado; labels são
   provider/model/outcome/kind/method/path/status — sem user_id, sem prompt,
   sem chaves. `set_breaker_state` e contadores em `resilience.py` idem. OK.
5. **OTel desligado por padrão** (`OTEL_ENABLED=false`, no-op real — imports só
   dentro do setup). Span custom `llm.generate` só carrega `provider` e `model`;
   nenhum header/API key/prompt é posto como atributo. FastAPI instrumentor exclui
   `/metrics,/health,/ready`. Atenção residual (aceitável, documentar — item 3
   abaixo): a auto-instrumentação do httpx registra a URL das chamadas de egress;
   Gemini pode carregar a API key na query string dependendo do provider — conferir
   que `providers.py` envia a key via header (`x-goog-api-key`) e não via `?key=`.
6. **pytest**: 65 passed, 4 skipped (exit 0). Testes de observabilidade cobrem
   headers em exceção não tratada e ausência do prompt no log.

## Ajustes solicitados

1. **[BLOQUEANTE] Cardinalidade do label `path` para rotas não casadas.**
   `_route_template()` em `app/core/middleware.py` cai para `scope["path"]` (path
   bruto) quando não há `route` no scope. Isso acontece em TODO 404 e em TODO 401
   (o ApiKeyAuthMiddleware responde antes do roteamento). Um cliente não
   autenticado pode varrer paths aleatórios (`/a1`, `/a2`, …) e criar séries
   ilimitadas em `http_requests_total`/`http_request_duration_seconds` →
   crescimento de memória do app e do Prometheus (DoS barato), além de refletir
   paths arbitrários controlados pelo atacante no /metrics (que é público na rede
   interna, isento de auth). Correção sugerida: quando `scope.get("route")` for
   None, usar um label fixo (ex.: `"unmatched"`) em vez do path bruto. O path
   bruto pode continuar no access log (cardinalidade de log não é problema).

## Recomendações (não bloqueantes)

2. **Grafana admin/admin e portas expostas — marcar explicitamente como dev-only.**
   `docker-compose.yml` e `docs/observability.md` documentam admin/admin e as
   portas 9090/3000/16686/4318 publicadas no host, mas sem um aviso explícito
   "somente desenvolvimento local; não usar em produção". Adicionar uma linha de
   aviso em `docs/observability.md` (e opcionalmente bind em 127.0.0.1).
3. **Documentar em docs/security.md ou observability.md** que, com OTel ligado, a
   instrumentação httpx registra URLs de egress — não colocar segredos em query
   string (já é o caso se as keys vão por header).

## Como validar após correção

- Novo teste: request para path inexistente sem API key → scrape de `/metrics`
  não deve conter o path bruto como label.
- `python -m pytest -q` verde.

## Rodada 2 — Veredito: APROVADO

Re-verificação em 2026-07-07:

1. **Item bloqueante resolvido.** `_route_template()` em `app/core/middleware.py`
   agora retorna o label fixo `"unmatched"` quando não há rota casada (404 e 401
   pré-roteamento) — nunca o path bruto. Teste novo
   `tests/test_observability.py::test_unmatched_paths_do_not_create_raw_path_series`
   cobre 404 e 401 e assevera que os paths arbitrários não aparecem no `/metrics`.
2. **Recomendação 2 atendida.** `docs/observability.md` traz aviso explícito
   "Somente desenvolvimento local" para o perfil `observability` (Grafana
   admin/admin, portas 9090/3000/16686/4318 sem auth).
3. **Recomendação 3 atendida.** Nota em `docs/observability.md`: URLs de egress
   aparecem nos spans httpx; API keys só via header, nunca na URL (consistente
   com `docs/security.md`).
4. **pytest**: 66 passed, 4 skipped (exit 0).
