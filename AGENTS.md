# AGENTS.md — Estúdio de Canais Dark

Fonte única de convenções, comandos, decisões e status do projeto. **Atualize este arquivo
a cada mudança de convenção/estrutura e obrigatoriamente ao fim de cada fase.**
Briefing original: `docs/briefing.md`. Roadmap: `docs/roadmap.md`.

## Visão
Sistema web (FastAPI + MCP) para gerenciar canais dark do YouTube e o pipeline de produção
de vídeos. O Claude atua via servidor MCP (`/mcp`); **a API do Claude nunca é usada**.

## Status
Fase 0 (Fundação) em andamento. Ver `docs/roadmap.md`.

## Fluxo de trabalho
- Uma fase por vez: plano → aprovação → implementação → testes → docs → resumo.
- Commits pequenos (Conventional Commits), testes verdes antes de commitar.
