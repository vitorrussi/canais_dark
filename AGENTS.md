# AGENTS.md — Estúdio de Canais Dark

Fonte única de convenções, comandos, decisões e status. **Atualize a cada mudança de
convenção/estrutura e obrigatoriamente ao fim de cada fase.** Briefing: `docs/briefing.md`.
Roadmap: `docs/roadmap.md`. Guia: `docs/development.md`. Arquitetura: `docs/architecture.md`.

## Visão
Sistema web (FastAPI + MCP) para gerenciar canais dark do YouTube e o pipeline roteiro →
narração → imagens → render → Shorts → upload. O Claude atua via servidor MCP em `/mcp`;
**a API do Claude nunca é usada**. Roda em servidor doméstico via Cloudflare Tunnel, 2 usuários.

## Status
- Fase 0 (Fundação): **concluída**. Próxima: Fase 1 (canais e vídeos) — planejar e aguardar aprovação.

## Stack
Python 3.12+, uv, FastAPI, SQLModel + SQLite (WAL) + Alembic, pydantic-settings, structlog,
Typer, Jinja2 + HTMX (UI), SDK `mcp` (Fase 2), ffmpeg (Fase 5), pytest/ruff/mypy.

## Comandos
`uv sync` · `make dev` (porta 8123) · `make test` · `make lint` · `make fmt` · `make migrate` ·
`uv run canaisdark create-user --name N --email E` · `uv run canaisdark rotate-token --email E`

## Estrutura
`src/canaisdark/`: `config.py`, `logging.py`, `db.py`, `models/`, `services/` (lógica),
`api/` (REST + `deps.py` auth), `main.py` (`create_app()`), `cli.py`. Previstos: `mcp/`, `web/`,
`worker/`, `integrations/`, `schemas/`. Migrações em `migrations/`. Testes em `tests/`.

## Convenções
- **Regra de ouro:** lógica só em `services/`; REST/MCP/web/worker/CLI são cascas finas.
  Services recebem `Session` explícita e não importam FastAPI/MCP.
- Type hints em tudo (mypy strict em `src`); ruff para lint/format (linha 100).
- Tools MCP e jobs nunca bloqueiam (>~100 s é cortado pelo Cloudflare): criam Job e retornam `job_id`.
- Nada de render/TTS dentro de request HTTP. Segredos só em `.env`.
- Integrações externas mockadas nos testes; sem rede.
- Migrações: gerar com autogenerate e **revisar**; SQLite usa batch mode.
- Commits pequenos, Conventional Commits, testes verdes; trailer Co-Authored-By conforme o ambiente.
- Fluxo: uma fase por vez → plano → aprovação → implementar → testes → atualizar docs/AGENTS.md.

## Decisões
Ver `docs/decisions/` (fila no banco, HTMX, MCP SDK oficial stateless). Auth: Bearer `cd_...`
com hash SHA-256 (`services/users.py`).

## Fora de escopo
ComfyUI, dublagem, OAuth no MCP, analytics do YouTube (deixar pontos de extensão apenas).
