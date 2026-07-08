# Contexto Base — compartilhado por TODOS os agentes

CONTEXTO DO PROJETO:
Estou construindo um micro-serviço para um desafio técnico com os seguintes requisitos:
- Receber prompts de usuários via API REST (POST /v1/chat), com payload de entrada
  { "userId": "...", "prompt": "..." } e resposta contendo
  { id, userId, prompt, response, model, timestamp } (payloads podem ser melhorados).
- Persistir prompt e resposta em banco de dados para análises futuras.
- Invocar um LLM em tempo real via OpenRouter (free tier) com fallback para Gemini Free Tier.
- A solução deve ser resiliente, segura, observável e performática.

DECISÕES DE STACK (usar sempre, para manter consistência entre agentes):
- Python 3.12 + FastAPI + Uvicorn, gerenciado com uv (ou pip + requirements.txt)
- PostgreSQL 16 como banco principal, acesso via SQLAlchemy 2.0 async + Alembic (migrations)
- httpx (async) para chamadas ao LLM
- Docker + docker-compose para rodar localmente
- pytest + pytest-asyncio para testes; ruff para lint/format; mypy para tipos
- Estrutura do projeto: app/ (api/, core/, services/, repositories/, models/, schemas/),
  tests/, docs/, migrations/
- Configuração 100% via variáveis de ambiente (pydantic-settings), com .env.example no repo
  e NENHUM segredo commitado.

O diretório do projeto é a raiz deste repositório. Trabalhe de forma autônoma: não pare
para pedir confirmação; tome decisões razoáveis e documente-as. Ao final, liste o que
foi criado/alterado e como validar.
