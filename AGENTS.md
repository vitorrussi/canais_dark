# AGENTS.md — Estúdio de Canais Dark

Fonte única de convenções, comandos, decisões e status. **Atualize a cada mudança de
convenção/estrutura e obrigatoriamente ao fim de cada fase.** Briefing: `docs/briefing.md`.
Roadmap: `docs/roadmap.md`. Guia: `docs/development.md`. Arquitetura: `docs/architecture.md`.

## Visão
Sistema web (FastAPI + MCP) para gerenciar canais dark do YouTube e o pipeline roteiro →
narração → imagens → render → Shorts → upload. O Claude atua via servidor MCP em `/mcp`;
**a API do Claude nunca é usada**. Roda em servidor doméstico via Cloudflare Tunnel, 2 usuários.

## Status
- Fases 0 (Fundação), 1 (Canais e vídeos) e 2 (MCP + ClaudeTasks): **concluídas**. Próxima: Fase 3 (Jobs + ElevenLabs) — exige chave da API do ElevenLabs e voice_id; avisar o usuário antes de planejar.

## Stack
Python 3.12+, uv, FastAPI, SQLModel + SQLite (WAL) + Alembic, pydantic-settings, structlog,
Typer, Jinja2 + HTMX (UI), SDK `mcp` 2.x (`MCPServer`), ffmpeg (Fase 5), pytest/ruff/mypy.

## Comandos
`uv sync` · `make dev` (porta 8123) · `make test` · `make lint` · `make fmt` · `make migrate` ·
`uv run canaisdark create-user --name N --email E` · `uv run canaisdark rotate-token --email E`

## Estrutura
`src/canaisdark/`: `config.py`, `logging.py`, `db.py`, `models/`, `services/` (lógica),
`schemas/` (Pydantic), `api/` (REST Bearer; `deps.py` auth), `web/` (Jinja2+HTMX, login por cookie de sessão,
static vendorizado), `main.py` (`create_app()`), `cli.py`. `mcp/` (server.py tools, auth.py, context.py), `services/tasks.py`/`scripts.py`/`task_context.py`.
Previstos: `worker/`, `integrations/`. Migrações em `migrations/`. Testes em `tests/`.

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
- MCP: ver `docs/mcp-tools.md` e ADR 0003. Tools = cascas finas; erros de domínio → `ToolError`. Nunca bloquear.
- ClaudeTask: reserva atômica (UPDATE condicional), TTL 30 min, 1 tarefa aberta por (vídeo, tipo). Tipos em inglês (`generate_script`…).
- Roteiro: sempre versão nova (nunca sobrescreve); aprovação só humana (site/REST), uma versão aprovada por vídeo.
- Criar vídeo enfileira `generate_script`. Apagar rascunho remove cenas/versões/tarefas antes (FKs).
- UI: login colando o token de API → cookie de sessão assinado (`SECRET_KEY`, SameSite=Lax; obrigatório fora de dev).
  Mapeamento por Cloudflare Access fica para a Fase 9. Todos os usuários veem tudo (sem ACL).
- Canal: slug imutável (nome de pasta em `DATA_DIR/<slug>/<video_id>`); arquiva em vez de apagar.
  Vídeo só é apagado em `draft`. `Video.status` é string (valores de `VideoStatus`) para evitar migração a cada novo estado.
- Etapas do vídeo: `services/video_status.py` (`STAGES` + `_STATE_CALCULATORS`); cada fase registra o calculador da sua etapa.
- Erros de domínio (`services/errors.py`) → 404/409/422 no handler de `main.py`.
Ver `docs/decisions/` (fila no banco, HTMX, MCP SDK oficial stateless). Auth: Bearer `cd_...`
com hash SHA-256 (`services/users.py`).

## Fora de escopo
ComfyUI, dublagem, OAuth no MCP, analytics do YouTube (deixar pontos de extensão apenas).
