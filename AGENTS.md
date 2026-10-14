# AGENTS.md — Estúdio de Canais Dark

Fonte única de convenções, comandos, decisões e status. **Atualize a cada mudança de
convenção/estrutura e obrigatoriamente ao fim de cada fase.** Briefing: `docs/briefing.md`.
Roadmap: `docs/roadmap.md`. Guia: `docs/development.md`. Arquitetura: `docs/architecture.md`.

## Visão
Sistema web (FastAPI + MCP) para gerenciar canais dark do YouTube e o pipeline roteiro →
narração → imagens → render → Shorts → upload. O Claude atua via servidor MCP em `/mcp`;
**a API do Claude nunca é usada**. Roda em servidor doméstico via Cloudflare Tunnel, 2 usuários.

## Status
- Fases 0–8 (incl. extensões 2.1–2.5) **concluídas**. Próxima: Fase 9 (Deploy).

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
**Fase 3 (Jobs + ElevenLabs) concluída**: fila `Job` + worker (`make worker`), narração (TTS) com guardas de crédito, Forced Alignment OPCIONAL (global/canal/geração; sem ele tempos estimados),
legendas `.srt/.ass`, invalidação automática, card de Narração com progresso, `/jobs`, REST e MCP `get_job_status/list_jobs`. Detalhes: `docs/jobs-and-narration.md`.
Dois processos: `make serve` + `make worker`. `ELEVENLABS_FAKE=true` = demonstração sem créditos. Testes usam `FakeElevenLabs` (nunca rede). Segredo `ELEVENLABS_API_KEY` só no `.env`.
Cancelamento/desfazer: `Job.run_after` (janela `NARRATION_UNDO_SECONDS`), TTS por streaming com `should_cancel`, `TTS_CHUNK_CHARS=1500`. Escolha de voz/modelo: `web/voices.py`, `services/voices.py`. Plano grátis NÃO usa vozes da biblioteca via API.
**Fase 4 (Prompts e assets) concluída**: `Scene.image_prompt/video_prompt/wants_vertical/is_key_scene`, `services/scene_prompts.py` (compose, save, carry-over ao aprovar, pacote), `services/scene_assets.py` (upload validado por conteúdo, miniaturas Pillow, substituir/remover, lote por nome), storyboard `/videos/<id>/scenes` (`web/scenes.py`, `static/scenes.js`), MCP `save_scene_prompts`. Detalhes: `docs/prompts-and-assets.md`. Upload = 1 arquivo/requisição (Cloudflare 100 MB).
**Fase 5 (Timeline e render) concluída**: `schemas/timeline.py` (JSON versionado), `services/timeline.py` (padrão automática, validação, versões como ativo `timeline`), `services/render.py` (segmentos por cena com crossfade embutido e cache por hash, execução paralela, passo final com trilha/ducking/legendas/textos), `services/music.py`, job `render` (`worker/handlers.py:run_render`), etapas `timeline`/`render`/`review`, cartão **④** em `/videos/{id}` + `/videos/{id}/timeline` (`web/render.py`), REST `api/render.py`, MCP `get_timeline/save_timeline/start_render` e tarefa `generate_timeline`. Detalhes: `docs/render.md`. Precisa de **ffmpeg** (instalado). Testes de render reais usam ffmpeg (skip se ausente); `tests/media.py` cria vídeos de teste. Mudou a montagem dos segmentos → incremente `ALGO_VERSION`.
**Fase 6 (Shorts 9:16, etapa OPCIONAL) concluída**: seleção versionada `shorts_plan` (`services/shorts.py`: localiza `exact_text` no alinhamento, valida 15–59 s), render `services/shorts_render.py` (job `shorts`, layout `fill` com imagem vertical ou `blur` com 16:9, legendas palavra a palavra `subtitles.to_ass_shorts`, fatia da narração + trilha), ativos `short`, estágio `shorts` (`skipped` = dispensada; nada depende dele), cartão **⑤** recolhido em `/videos/{id}` (`web/shorts.py`), REST `api/shorts.py`, MCP `get_shorts/save_short_selections/start_shorts` e tarefa `select_shorts` (só quando a pessoa pede). Detalhes: `docs/shorts.md`. `ALGO_VERSION`=2.
**Fase 7 (YouTube) concluída**: OAuth por canal (`services/youtube.py`, tabela `youtube_account`, refresh token Fernet com `FERNET_KEY`, `state` assinado), cliente `integrations/youtube.py` (upload resumable; `FakeYouTube`, `YOUTUBE_FAKE=true`), metadados como proposta versionada (`services/metadata.py`, MCP `get_metadata/save_metadata`; a pessoa aplica), thumbnail (`services/thumbnail.py`), job `youtube_upload` **sempre privado** com guarda de cota/duplicata (só site/REST: **nenhuma tool MCP envia ao YouTube**), cartão **⑥** em `/videos/{id}` e bloco YouTube no canal. Testado só com cliente falso/MockTransport: o teste real exige `GOOGLE_CLIENT_ID/SECRET` + `FERNET_KEY` (passo a passo em `docs/youtube.md`). Detalhes: `docs/youtube.md`.
**Fase 8 (Google Drive, OPCIONAL) concluída**: conexão por canal (`services/drive.py`, tabela `drive_account`, escopo `drive.file`, token Fernet), pastas criadas pelo app (`Channel.drive_folder_id`, `Video.drive_folder_id`, `Video.drive_url`), manifesto por opções (vídeo, Shorts, thumbnail, roteiro, legendas, narração; mídia opcional), job `drive_sync` incremental (carimbo+SHA-256, atualiza o mesmo arquivo, recria pasta/arquivo apagados; só site/REST, nenhuma tool MCP), cliente `integrations/drive.py` (`FakeDrive`, `DRIVE_FAKE=true`), cartão **⑦** em `/videos/{id}` e bloco no canal. Testado só com cliente falso/MockTransport; teste real precisa da Drive API ativada no projeto Google e do redirect `/oauth/drive/callback` (`docs/drive.md`).
**Claude sem terminal (concluída)**: (1) conector por URL com OAuth no `/mcp` (SDK `mcp.server.auth`; `services/oauth.py`, `mcp/oauth_provider.py`, `mcp/oauth_routes.py`, tela `/oauth/consent`, tabelas `oauth_client/oauth_token`; token `cd_...` segue valendo; rotacionar token/desativar derruba conectores; link de acesso em `services/invites.py`, `/invite/<assinado>`, botão em `/admin/users`; retorno pós-login via `session["next"]`); (2) modo copiar/colar sem MCP (`services/offline_tasks.py`, `/videos/{id}/claude/{tipo}`, REST `api/offline.py`). Detalhes: `docs/connect-claude.md`. Os testes isolam GOOGLE_*/FERNET_* do `.env` (`tests/conftest.py`).
**Próxima: Fase 9 (Deploy)** — serviço no servidor (systemd/Docker Compose), Cloudflare Tunnel, Access (site com login, `/mcp` com Service Token), backup do banco e dos arquivos.

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
ComfyUI, dublagem, analytics do YouTube (deixar pontos de extensão apenas).
