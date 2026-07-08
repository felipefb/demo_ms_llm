# Agente 3 — Integração com LLM (OpenRouter + fallback Gemini)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Implementar o cliente LLM resiliente.

1. Crie uma abstração LLMProvider (interface com generate(prompt, model) -> LLMResult
   contendo text, model, provider, usage) e duas implementações:
   - OpenRouterProvider: POST https://openrouter.ai/api/v1/chat/completions,
     auth via env OPENROUTER_API_KEY, modelo default configurável por env
     (ex.: um modelo :free do catálogo).
   - GeminiProvider: API generateContent do Gemini (env GEMINI_API_KEY),
     modelo default gemini-1.5-flash ou mais novo.
2. Resiliência (biblioteca tenacity + implementação própria onde fizer sentido):
   - Timeout total por chamada configurável (default 30s) via httpx.
   - Retry com backoff exponencial + jitter APENAS para erros transitórios
     (timeout, 429, 5xx) — nunca para 4xx de validação. Máx. 2 retries.
   - Circuit breaker simples por provider (abre após N falhas consecutivas,
     half-open após cooldown) — pode usar a lib `purgatory` ou implementar.
   - Fallback: se OpenRouter falhar/circuito aberto, tenta Gemini; se ambos falharem,
     retorna 503 com envelope de erro padronizado e a interação fica status=failed
     no banco (o prompt nunca se perde).
3. Reuse um único httpx.AsyncClient (connection pool) criado no lifespan da app.
4. Registre em cada resultado: provider usado, modelo, latência e tokens.
5. Testes com respx (mock do httpx): sucesso OpenRouter, 429→retry→sucesso,
   OpenRouter caindo→fallback Gemini, ambos caindo→503, timeout.

CRITÉRIOS DE ACEITE: pytest verde sem tocar rede real; com as duas API keys no .env,
um curl em /v1/chat retorna resposta real do LLM; derrubando a key do OpenRouter,
o fallback para Gemini funciona.
