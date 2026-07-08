# Parecer — validação cruzada: observabilidade sobre a entrega de security_hardening

Revisor: app_observability (Fase 3, Rodada 1) — 2026-07-07
Escopo revisado: `app/core/auth.py`, `app/core/ratelimit.py`, `app/core/security_headers.py`,
`app/main.py`, `docs/security.md`, `tests/test_security.py`.

## Veredito: APROVADO

## Verificações (todas OK)

1. **Tipo de middleware:** os três middlewares de segurança são ASGI puros (sem
   BaseHTTPMiddleware) — não quebram contextvars nem o pipeline de streaming.
2. **Ordem preserva a observabilidade:** `RequestIdMiddleware` é o mais externo
   (adicionado por último; add_middleware é LIFO). Ordem em runtime:
   RequestId → CORS → SecurityHeaders → BodyLimit → Auth → RateLimit.
   Consequência: qualquer 401/429/413/415 emitido pelos middlewares de segurança
   passa pelo `send_wrapper` do RequestId e portanto:
   - recebe `X-Request-ID` e `X-Response-Time-Ms`;
   - é contado em `http_requests_total` / `http_request_duration_seconds`;
   - gera linha de access log `http_request` com request_id, status e latency_ms.
3. **request_id no envelope de erro:** `set_request_id()` roda antes dos middlewares
   internos; `_error_response`/`_envelope_bytes` leem o contextvar via
   `get_request_id()` — 401/429/413/415 retornam
   `{"error": {code, message, request_id}}` com o id correto. Coberto por
   `test_request_without_api_key_returns_401` (asserta `request_id` no body).
4. **Logs de auth:** key mascarada (`mask_key`: 4 chars + tamanho); warning registra
   path e motivo (missing/invalid). Loggers stdlib (`app.auth`, `app.ratelimit`,
   `app.security`) passam pelo `foreign_pre_chain` do structlog → saem em JSON e
   ganham `request_id` automaticamente (`_add_request_id`). Suficiente para
   troubleshooting sem vazar segredo.
5. **Visibilidade do rate limiter:** warning log por rejeição (com retry_after) +
   rejeições visíveis em `http_requests_total{status="429"}` via middleware de
   métricas. Também recebe headers de segurança (SecurityHeaders é mais externo).
6. **Headers de segurança em respostas de erro:** 401/429/413/415 passam pelo
   `send_wrapper` do SecurityHeaders → nosniff/DENY/CSP/no-store presentes.
7. **/metrics em PUBLIC_PATHS:** correto para o scraper interno; risco documentado
   em docs/security.md.
8. **pytest:** `66 passed, 4 skipped` (inclui os 15 de test_security.py).

## Observações não bloqueantes (donos sugeridos entre parênteses)

- **Cardinalidade do label `path` em rejeições pré-roteamento:** 401/429 curto-circuitam
  antes do router, então `scope["route"]` não existe e o label cai no path bruto.
  Para rotas legítimas o path == template; porém um scanner sem key batendo em paths
  aleatórios cria uma série nova por path em `http_requests_total{status="401"}`.
  Mitigação sugerida: no `_route_template` (app/core/middleware.py), colapsar paths
  sem route match para `"unmatched"` quando status ∈ {401, 404}. (dono: observability —
  é código do middleware de métricas, não da entrega de security.)
- **415 só quando Content-Type presente:** POST sem Content-Type passa do BodyLimit e
  vira 422 no FastAPI. Comportamento aceitável (erro ainda é observável e envelopado),
  apenas registrado.
- **Rate limit sem métrica dedicada:** `http_requests_total{status="429"}` cobre o
  requisito; um contador `rate_limit_rejections_total` seria nice-to-have, não exigido.

## Conclusão

A entrega de segurança preserva integralmente request_id, access log e métricas HTTP
para todas as respostas que emite; logs mascaram a key e mantêm contexto; envelope de
erro padrão com request_id confirmado por teste. Nenhum ajuste bloqueante.
