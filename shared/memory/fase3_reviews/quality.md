# Parecer — Validação de Qualidade (quality_ci) — Fase 3

Time: `teams/team-fase3-hardening-obs.yml` — escopo: qualidade/testes das entregas
de security_hardening e app_observability. CI/Makefile ficam para a Fase 4.

## Rodada 1

### Suíte de testes
- `.venv\Scripts\python -m pytest -q` → **66 passed, 4 skipped** (skips = testes
  que exigem rede; nenhuma chamada externa executada). Nota: o board dizia
  "65 passed" — o número real após a entrega final da observabilidade é 66
  (test_observability.py tem 5 casos, não 4). Contagem estável em 3 execuções.
- Coleta: 70 testes.

### Cobertura dos cenários exigidos — todos presentes
| Cenário | Teste |
|---|---|
| 401 sem key / key errada | tests/test_security.py:16,24,30 |
| Key válida passa / rotas públicas | tests/test_security.py:35,40 |
| 429 + Retry-After (e isenção de paths públicos) | tests/test_security.py:55,68 |
| 413 body grande / 422 prompt > schema / 415 content-type | tests/test_security.py:79,86,92 |
| Headers de segurança | tests/test_security.py:108 |
| Fail-fast API_KEY_HASH (ausente/inválido) | tests/test_security.py:120,129 |
| Métricas custom em /metrics | tests/test_observability.py:10,29 |
| Access log JSON com request_id, sem body | tests/test_observability.py:56 |
| X-Request-ID em exceção não tratada | tests/test_observability.py:88 |

### Lint / format (achados e correções aplicadas — mandato de higiene)
1. `ruff format --check` reprovava em 3 arquivos → **apliquei `ruff format`** em
   `app/core/auth.py`, `app/core/config.py`, `tests/test_security.py` (sem
   mudança de comportamento).
2. `ruff check` acusava 4× B008 (`Depends(...)` em default) em
   `app/api/v1/chat.py` — padrão idiomático do FastAPI; **adicionei
   `ignore = ["B008"]`** com comentário em `pyproject.toml` (`[tool.ruff.lint]`).
3. Após correções: `ruff check app tests` → All checks passed;
   `ruff format --check` → 40 files already formatted.

### Arquitetura / duplicação / dead code
- Sem duplicação entre middlewares: `middleware.py` (request_id + timing +
  access log + métricas HTTP, ASGI puro com garantia em exceção),
  `security_headers.py` (headers + limites de body/content-type),
  `auth.py` / `ratelimit.py` separados. Responsabilidades disjuntas.
- Detalhe positivo: label `path` das métricas usa route template com fallback
  "unmatched" — evita explosão de cardinalidade por scanners.
- conftest coeso após TEST_API_KEY: hash derivado da key, header default no
  `make_client`, `RATE_LIMIT_REQUESTS=1000` para não interferir nos testes
  funcionais; sem rede e sem banco real por padrão.
- Sem imports mortos/dead code (ruff F/E/B/UP/I limpos).

Veredito Rodada 1: AJUSTES (apenas higiene lint/format, aplicados por mim).

## Rodada 2 (pós-correções de higiene)
- Re-execução: `pytest -q` → 66 passed, 4 skipped. ruff check/format limpos.

**Veredito: APROVADO**
