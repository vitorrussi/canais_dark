# AGENTS.md — Estúdio de Canais Dark

Fonte única de convenções, comandos, decisões e status. **Atualize a cada mudança de
convenção/estrutura e obrigatoriamente ao fim de cada fase.** Briefing: `docs/briefing.md`.
Roadmap: `docs/roadmap.md`. Guia: `docs/development.md`. Arquitetura: `docs/architecture.md`.

## Visão
Sistema web (FastAPI + MCP) para gerenciar canais dark do YouTube e o pipeline roteiro →
narração → imagens → render → Shorts → upload. O Claude atua via servidor MCP em `/mcp`;
**a API do Claude nunca é usada**. Roda em servidor doméstico via Cloudflare Tunnel, 2 usuários.

## Status
- Fases 0, 1 e 2 (incl. extensões 2.1–2.4: UI, editor, import/export, QoL): **concluídas**. Próxima: Fase 3 (Jobs + ElevenLabs) — exige chave da API do ElevenLabs e voice_id; avisar o usuário antes de planejar.

## Stack
Python 3.12+, uv, FastAPI, SQLModel + SQLite (WAL) + Alembic, pydantic-settings, structlog,
Typer, Jinja2 + HTMX (UI), SDK `mcp` 2.x (`MCPServer`), ffmpeg (Fase 5), pytest/ruff/mypy.

## Comandos
`uv sync` · `make dev` (porta 8123) · `make test` · `make lint` · `make fmt` · `make migrate` ·
`uv run canaisdark create-user --name N --email E` · `uv run canaisdark rotate-token --email E`

## UI (Etapa 2.1)
Tema próprio em `web/static/app.css` (escuro por padrão, alterna claro/escuro; sem framework CSS), `app.js` (copiar via
`data-copy`, confirmar via `data-confirm`, presets via `data-fill`, toasts), macros em `web/templates/_macros.html`
(`ic()` ícones SVG, `field()` campo com ajuda+exemplo). Feedback ao usuário: `_back(request, url, ok=/error=)` → toast.
Presets de canal em `web/presets.py`. Erros de domínio em páginas HTML renderizam `error.html`.
Etapa 2.2 (concluída): editor de roteiro em `/videos/{id}/script/edit` (`web/scripts.py`, `static/script_editor.js`,
`script_editor.html`): modos cenas/texto corrido, dividir/mesclar/frases/duplicar/mover, localizar-substituir (regex),
limpeza, desfazer/refazer, rascunho local (localStorage), salvar = NOVA versão (`user:<id>`, opção aprovar). Comparação de versões em
`/videos/{id}/diff?a=&b=` (`services/script_diff.py`). Texto: `services/script_text.py`. REST: `POST /api/videos/{id}/scripts`.
Fonte da verdade no JS é o array `scenes`; `window.__editor` expõe a API para depuração/testes manuais no navegador.
Etapa 2.3 (concluída): baixar roteiro (`/scripts/{id}/download?format=txt|md|json`), importar texto/arquivo com regras de divisão +
prévia (`/videos/{id}/script/import`), criar vídeo com roteiro pronto sem acionar o Claude (`/channels/{slug}/videos/manual`),
exportar/importar vídeo e canal (`/videos/{id}/export`, `/channels/{slug}/export`, `/import`). Formato JSON versionado
(`canaisdark.video|channel|script`, v1) em `schemas/transfer.py`; lógica em `services/transfer.py` e `services/script_formats.py`;
rotas em `web/transfer.py` e `api/transfer.py`. Importar SEMPRE cria algo novo (autor `import:<id>`), nunca sobrescreve; canal importado
sem nome explícito ganha sufixo " (2)". `htmx-config` em `base.html` faz o htmx trocar respostas 404/422 (senão erros de formulário somem).
Etapa 2.4 (concluída): `/connect` (comando `claude mcp add` pronto + gerar/rotacionar token; o valor aparece uma única vez), campos extras do canal
(`words_per_scene`, `script_rules`, `default_script_instructions` → vão no contexto de `claim_next_task`), editar/duplicar vídeo, busca (`?q=`) e filtro
de status, desarquivar canal, instruções extras por vídeo (`VideoCreate.instructions`), painel inicial (`services/dashboard.py`: "precisa da sua
atenção" + atividade recente), atalhos de teclado (g c/p/k, /, n, ?), prompt "processar tudo" em `/tasks`.
Cuidado: chaves de contexto de template não podem colidir com parâmetros de `render()` (ex.: use `status_filter`, nunca `status`).
Etapa 2.5 (polimento visual, concluída): sem overflow horizontal no celular (testado em 11 páginas com iframe de 390px), `select` com seta própria,
favicon, barra de carregamento do htmx, proteção contra envio duplo, foco visível, barra de salvar fixa nos formulários de canal, menus que fecham ao clicar fora,
`prefers-reduced-motion`. Dica de teste responsivo: `resize_window` do Chrome não muda o viewport; use iframes de 390px e compare `scrollWidth` x `clientWidth`.
**Fase 2 e extensões 2.1–2.5 concluídas.** Próxima: Fase 3 (Jobs + ElevenLabs) — exige chave da API e `voice_id`; avisar o usuário antes de planejar.

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
