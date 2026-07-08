# Parecer de Segurança — Fase 6, Rodada 1 (packaging_docs)

Revisor: security_hardening
Data: 2026-07-07

## Veredito: APROVADO

## Itens verificados

1. **Dockerfile** (`Dockerfile`)
   - Multi-stage: builder (venv em /opt/venv) → runtime `python:3.12-slim`;
     pip/ferramentas de build não vão para a imagem final. OK.
   - Non-root de verdade: `groupadd/useradd --system` e `USER app` (linha 40)
     ANTES do `HEALTHCHECK` e `CMD`; sem sudo/su, sem escalação. OK.
   - `COPY` restrito a app/, migrations/, alembic.ini. OK.
   - `HEALTHCHECK` via stdlib urllib em 127.0.0.1:8000/health — sem curl,
     sem vazar dados (endpoint /health não é autenticado por design). OK.

2. **.dockerignore** — exclui `.git`, `.env`, `.env.*` (com exceção apenas de
   `.env.example`), `tests/`, docs, shared/agents/teams, caches. Contexto de
   build mínimo. OK.

3. **docker-compose.yml**
   - Postgres em `127.0.0.1:${POSTGRES_PORT:-5432}:5432` — não exposto na rede. OK.
   - Senha via `${POSTGRES_PASSWORD:-postgres}` com comentário explícito de que
     o default é só para dev; propagada corretamente ao DATABASE_URL. OK.
   - `API_KEY_HASH` vindo do ambiente, sem placeholder de segredo commitado
     (default vazio = auth off apenas em APP_ENV=dev; fora de dev o app
     falha fast na inicialização). OK.
   - Perfil observability: Prometheus/Grafana/Jaeger em bind 127.0.0.1, com
     aviso explícito de credenciais default e uso apenas local. OK.
   - Porta da API `${APP_PORT:-8000}:8000` publicada em todas as interfaces —
     intencional (é o serviço exposto) e protegido por auth. Aceitável.

4. **README.md** — nenhum segredo real; instrui gerar `API_KEY_HASH` via
   sha256 (chave de exemplo `minha-chave-secreta` claramente de exemplo);
   documenta que auth desabilitada só vale em dev. OK.

5. **LICENSE** — MIT, Felipe Barros, 2026. OK.

6. **Segredos no repo** — `.env` no `.gitignore` (`.env`, `.env.*`,
   `!.env.example`); `git ls-files` só rastreia `.env.example` (sem valores
   reais, `API_KEY_HASH=` vazio). `git status` sem `.env`. OK.

## Pendências herdadas — status

- Dockerfile baseline root → **FECHADA** (multi-stage slim non-root + healthcheck).
- Compose bind/senha postgres → **FECHADA** (127.0.0.1 + senha parametrizada).

## Observações (não bloqueantes)

- Validação do `docker compose up --build` em ambiente com Docker segue
  pendente (máquina do builder sem Docker) — recomendo ao quality_ci ou ao
  final_reviewer executar quando possível.
- Grafana admin/admin permanece, mas restrito a loopback e com aviso — aceito
  para escopo de desafio local.
