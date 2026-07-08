# Agente 4 — Segurança

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Endurecer a segurança do micro-serviço.

1. Autenticação por API key: header X-API-Key validado contra hash (não plaintext)
   configurado por env var; aplicar em todos os endpoints exceto /health, /ready e /docs
   (docs podem ser desabilitados por env em produção).
2. Rate limiting por API key + IP (slowapi ou implementação com Redis se já existir;
   caso contrário, in-memory com limites configuráveis, ex.: 10 req/min) retornando 429
   com Retry-After.
3. Sanitização e limites de entrada: tamanho máximo do body, rejeição de content-type
   inesperado, validação estrita dos schemas (extra="forbid").
4. Proteções de resposta: headers de segurança (via middleware), CORS restritivo
   configurável, nunca vazar stack trace ou detalhes internos nos erros (conferir os
   exception handlers), logs sem dados sensíveis (mascarar API keys, truncar prompts
   nos logs).
5. Segredos: garantir .env no .gitignore, .env.example completo, README com aviso;
   validar na inicialização que as keys obrigatórias existem (fail fast com mensagem clara).
6. Mitigações de prompt injection no uso do LLM: system prompt fixo definido no servidor,
   prompt do usuário sempre enviado como mensagem de usuário (nunca concatenado a
   instruções), limite de tamanho, e documentar as limitações em docs/security.md.
7. Adicione checagens automatizadas: bandit (SAST) e pip-audit (vulnerabilidades de
   dependências) como comandos no CI/Makefile.
8. Testes: request sem API key→401, key errada→401, rate limit→429, body gigante→413/422.

CRITÉRIOS DE ACEITE: pytest verde; bandit e pip-audit sem findings de severidade alta;
docs/security.md descrevendo o modelo de ameaças resumido e as mitigações.
