# Agente 7 — Docker, DX e documentação final do repositório

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Empacotar e documentar o projeto para entrega.

1. Dockerfile multi-stage (builder + runtime slim, non-root user, healthcheck),
   imagem final enxuta. docker-compose final: app + postgres (+ perfil observability),
   com envs documentadas.
2. README.md completo em PT-BR (o repositório será avaliado por ele):
   - Visão geral do problema e da solução (1 parágrafo + diagrama simples da app)
   - Stack e decisões técnicas (tabela: escolha → justificativa)
   - Como rodar localmente: pré-requisitos, passo a passo com docker compose E sem
     Docker, como obter as API keys gratuitas (OpenRouter/Gemini), exemplo de curl
     do /v1/chat com resposta real
   - Como rodar testes, lint e o resto do Makefile
   - Estrutura de pastas comentada
   - Seções de segurança, resiliência e observabilidade (resumo + link para docs/)
   - Link para docs/architecture.md (Parte 2)
3. Confira que TUDO que o README promete funciona de ponta a ponta num ambiente limpo
   (simule: clone → .env → docker compose up → curl).
4. Prepare o repositório para publicação: git init se necessário, commits organizados
   por tema, LICENSE (MIT), e verifique que nenhum segredo está no histórico.

CRITÉRIOS DE ACEITE: uma pessoa que nunca viu o projeto consegue rodá-lo só com o
README; `docker compose up` + curl funciona; repositório pronto para `git push` público.
