# Parecer — final_reviewer (validador de consistência) — Fase 4, Rodada 1

Data: 2026-07-07. Alvo: entrega do builder `quality_ci` (board `shared/memory/fase4_board.md`).

## Veredito: APROVADO

## Verificações executadas (nesta máquina Windows, via `.venv\Scripts\python -m ...`)

| Alvo Makefile | Comando real | Resultado |
|---|---|---|
| lint | `ruff check app tests` + `ruff format --check app tests` | OK (all checks passed; 41 files formatted) |
| typecheck | `mypy app` | OK (Success: no issues in 30 source files; apenas uma *note* informativa em middleware.py) |
| test / cov | `pytest -q --cov=app --cov-report=term-missing` | OK — **77 passed, 4 skipped**; TOTAL **95.29%**, gate `fail_under=80` reproduzido, mensagem "Required test coverage of 80.0% reached" |
| security | `bandit -q -r app` + `pip_audit -r requirements.txt` | OK — bandit 0 findings; "No known vulnerabilities found" |

Os números reportados no board (95%, 77 passed / 4 skipped) são reproduzíveis.

## Consistência CI (`.github/workflows/ci.yml`)

- Jobs lint/typecheck/security/test usam exatamente os mesmos comandos do Makefile — sem drift.
- Paths referenciados existem: `requirements.txt`, `requirements-dev.txt`, `app/`, `tests/`.
- Python 3.12 no CI (pendência herdada da Fase 3 resolvida); service postgres:16 com healthcheck coerente.
- `python -m pip_audit` (underscore) é a invocação de módulo correta para o pacote `pip-audit`.

## Requirements / pyproject

- `requirements-dev.txt` lista pytest-cov, bandit, pip-audit — tudo que o CI instala e usa.
- `pyproject.toml` espelha os mesmos dev-deps e configura `[tool.coverage.report] fail_under = 80`. Coerente.

## tests/load

- `tests/load/README.md` bate com `chat_load.js`: 10 VUs, estágios 30s/1m/15s, envs `BASE_URL`/`API_KEY`, thresholds (`p(95)<3000`, `http_req_failed<0.05`, `checks>0.95`).
- Payload do script usa `user_id` (snake_case) e header `X-API-Key` — confere com `app/schemas/chat.py` e `app/core/auth.py`.
- Métricas citadas no README existem no código: `rate_limit_rejections_total`, `llm_latency_seconds`, `http_request_duration_seconds`, `circuit_breaker_state` (app/core/metrics.py, app/services/resilience.py).
- Env vars citadas existem: `RATE_LIMIT_REQUESTS` e `DB_POOL_SIZE` em config.py e `.env.example`; `DB_MAX_OVERFLOW` existe em config.py (nit: não listado em `.env.example`, mas funciona via pydantic-settings — não bloqueante).

## docs/security.md vs comportamento real

- Decisão documentada (sem Content-Type → 422; Content-Type não-JSON explícito → 415) tem teste de regressão: `tests/test_quality_gaps.py::test_post_without_content_type_returns_422` (existe e passa); 415 coberto em `test_security.py`.

## Observações não bloqueantes

1. `.env.example` não lista `DB_MAX_OVERFLOW`, citado em `tests/load/README.md` — cosmético.
2. mypy emite uma *note* `annotation-unchecked` em `app/core/middleware.py:85` — informativo, não falha o gate; já contemplado pelo racional "strict-ish" no pyproject.
