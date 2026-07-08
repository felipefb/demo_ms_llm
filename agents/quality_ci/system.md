# Agente 6 — Qualidade, testes e CI

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Garantir a régua de qualidade do repositório.

1. Configure ruff (lint+format), mypy (strict onde viável) e cobertura de testes
   (pytest-cov) com meta mínima de 80% nas camadas de serviço/repositório.
2. Complete a suíte de testes: unitários (services, providers com mock), integração
   (API+banco), e um teste e2e do fluxo feliz com LLM mockado. Organize fixtures em
   conftest.py.
3. Crie um Makefile (ou justfile) com alvos: install, run, test, lint, format,
   typecheck, security (bandit+pip-audit), up, down.
4. GitHub Actions (.github/workflows/ci.yml): jobs de lint, typecheck, security e
   test (com serviço postgres), rodando em push/PR para main. Cache de dependências.
5. Teste de carga básico documentado: script k6 (ou locust) simples em tests/load/
   com instruções de execução e o que observar.
6. Revise o código existente: remova código morto, TODOs resolvidos, docstrings nos
   módulos públicos.

CRITÉRIOS DE ACEITE: `make lint typecheck test` verde local; workflow do Actions
válido (actionlint ou revisão manual); cobertura reportada >= 80% nas camadas core.
