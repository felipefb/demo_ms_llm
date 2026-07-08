# Parecer de Segurança — Fase 4, Rodada 1 (validator: security_hardening)

Data: 2026-07-07. Escopo: entrega do builder `quality_ci` (CI, Makefile, k6, decisões 422/415 e contador de rate-limit).

## Veredito: APROVADO

## Checklist verificado

| Item | Resultado |
|---|---|
| Permissões mínimas no workflow | OK — `permissions: contents: read` no topo de `.github/workflows/ci.yml`; nenhum job eleva permissões |
| Segredos no CI | OK — nenhum `secrets.*` usado; nenhum segredo necessário (testes usam env de teste/echo client). `POSTGRES_PASSWORD: app` no service é placeholder efêmero de CI, aceitável |
| Jobs bandit/pip-audit | OK — job `security` roda `bandit -q -r app` e `pip_audit -r requirements.txt`; ambos retornam exit != 0 em findings, falhando o build. Mesmos comandos no alvo `security` do Makefile |
| Actions pinadas | OK (aceitável) — `actions/checkout@v4` e `actions/setup-python@v5` por major tag de actions oficiais GitHub. SHA-pinning seria mais forte (nota N1) |
| Cache | OK — `cache: pip` do setup-python; chave derivada de requirements*.txt, sem credenciais no conteúdo cacheado |
| k6 sem keys embutidas | OK — `tests/load/chat_load.js` lê `__ENV.API_KEY` com default inócuo `test-api-key`; README instrui passar via `-e API_KEY=` |
| Decisão 422 vs 415 | OK — não enfraquece o BodyLimitMiddleware: 415 mantido para Content-Type explicitamente não-JSON; 413 para body grande mantido; header ausente cai na validação Pydantic (422). Racional documentado em `docs/security.md` + teste de regressão |
| Contador `rate_limit_rejections_total{path}` | OK — registrado no registry dedicado (`app/core/metrics.py`), incrementado apenas no caminho 429 do `RateLimitMiddleware`; label `path` vem da rota (cardinalidade controlada pelo conjunto de rotas); nenhum dado sensível na métrica |
| Suíte de testes | OK — `77 passed, 4 skipped` confirmado localmente (5.5s) |

## Notas não bloqueantes

- N1: pinar actions por SHA (ex.: `actions/checkout@<sha> # v4`) para proteção contra tag re-point; baixo risco por serem actions oficiais.
- N2: `pip_audit -r requirements.txt` não cobre `requirements-dev.txt`; dev-deps não vão para a imagem de produção, mas rodam no CI — considerar auditar ambos.
- N3: `_MAX_TRACKED_KEYS` com `self._hits.clear()` reseta todas as janelas sob churn de 10k chaves — DoS residual teórico (atacante zera o próprio limite ao estourar o mapa). Já mitigado pelo design alvo (gateway/Redis) e documentado; sem ação nesta fase.
