# Quadro de Missão — Fase 4 (Qualidade, testes e CI)

Time: `teams/team-fase4-qualidade.yml`.

## Status

| Agente | Papel | Rodada | Veredito/Estado |
|--------|-------|--------|-----------------|
| quality_ci | builder | 1 | ENTREGUE (77 passed, 4 skipped; cobertura 95%) |
| security_hardening | validator | 1 | APROVADO (notas não bloqueantes) |
| final_reviewer | validator | 1 | APROVADO |

## Pendências herdadas (Fase 3)
- POST sem Content-Type → 422 vs 415: decidir e documentar (dono: quality_ci).
- Contador dedicado de rejeições de rate-limit (nice-to-have): decidir (dono: quality_ci).
- Alinhar versão Python do CI (venv local 3.11 vs contexto base 3.12).

## Registro de rodadas

### Rodada 1
- [quality_ci] Em execução — cobertura>=80%, Makefile, GitHub Actions, k6, revisão de código.
- [quality_ci] ENTREGUE (Rodada 1):
  - **Cobertura**: pytest-cov configurado (`[tool.coverage]`, fail_under=80 no total).
    Resultado: TOTAL 95% — services 86–99%, repositories 100%, api 100%, core 84–100%.
    Novos testes em `tests/test_quality_gaps.py` (health/ready degradado, tracing OTEL,
    PostgresConversationRepository com sessão fake, engine/factory, contador de rate-limit,
    POST sem Content-Type). Suíte: **77 passed, 4 skipped** (testcontainers sem Docker).
  - **mypy**: verde (`mypy app`), strict-ish via overrides em services/repositories/
    schemas/models (disallow_untyped_defs etc.); borda ASGI no modo padrão (documentado
    no pyproject). Um `type: ignore[arg-type]` em resilience.py (guard inatingível do tenacity).
  - **Makefile**: alvos install, run, test, cov, lint, format, typecheck, security, up, down —
    cada um é uma linha `python -m ...` executável direto no Windows sem make.
  - **CI** (`.github/workflows/ci.yml`): jobs lint, typecheck, security (bandit+pip-audit),
    test (service postgres:16; testcontainers detecta o Docker do runner ou faz skip —
    suíte verde em ambos os casos), Python 3.12, cache pip, push/PR para main. YAML validado.
  - **Carga**: `tests/load/chat_load.js` (k6, 10 VUs, thresholds) + `tests/load/README.md`
    (execução e o que observar em /metrics).
  - **Pendências herdadas resolvidas**:
    - POST sem Content-Type → mantido **422** (415 só para Content-Type explicitamente
      não-JSON); racional em docs/security.md + teste de regressão.
    - Contador dedicado de rate-limit **implementado**: `rate_limit_rejections_total{path}`
      (app/core/metrics.py + RateLimitMiddleware) com teste.
    - Python do CI = 3.12 (requires-python >=3.11 já compatível; venv local 3.11 segue válido).
  - **Revisão**: sem TODO/FIXME; docstrings adicionadas a chat.py (rotas), errors.py,
    request_id.py, main.py, schemas/chat.py. ruff check/format verdes; bandit 0 findings;
    pip-audit sem vulnerabilidades.

## Encerramento — FASE 4 CONCLUÍDA

Placar final: 2/2 APROVADO (security_hardening: workflow com permissões mínimas,
sem segredos, jobs de segurança bloqueantes; final_reviewer: todos os alvos do
Makefile executados e reproduzidos nesta máquina — lint/typecheck/cov/security
verdes, cobertura 95.29% com fail_under=80).
Suíte: 77 passed, 4 skipped. Gate da Fase 5 liberado.
Notas não bloqueantes registradas: SHA-pinning das actions, pip-audit em
requirements-dev, DB_MAX_OVERFLOW ausente do .env.example, note do mypy em
middleware.py:85 (candidatas ao final_reviewer/Fase 7).
