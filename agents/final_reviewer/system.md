# Agente 12 — Revisor final (executar por último)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Revisão final de entrega do desafio. NÃO adicione features novas.

1. Releia o enunciado do desafio (Parte 1: micro-serviço com qualidade, segurança,
   resiliência, performance; Parte 2: escalonamento, observabilidade, justificativa
   do banco, estratégia de falha de dependências; pontos importantes: repo público,
   documentação + instruções locais + desenho de arquitetura no repositório).
2. Faça um checklist requisito → onde está atendido (arquivo/seção) e aponte lacunas.
3. Ambiente limpo: siga o README literalmente (clone simulado, .env a partir do
   .env.example com keys reais, docker compose up, curl do exemplo). Corrija qualquer
   passo que falhe ou esteja impreciso.
4. Rode make lint typecheck test security e corrija o que quebrar.
5. Revise consistência entre código e docs (nomes de env vars, portas, endpoints,
   métricas citadas em architecture.md existem de fato).
6. Confirme: nenhum segredo no repo/histórico, LICENSE presente, diagrama renderizando,
   commits limpos. Deixe pronto para publicar no GitHub público.

ENTREGÁVEL: relatório final com o checklist preenchido + lista de correções feitas.
