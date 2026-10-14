# Ferramentas MCP

Servidor em `/mcp` (Streamable HTTP, stateless), autenticado com `Authorization: Bearer <token do usuário>`.
Implementação: `src/canaisdark/mcp/server.py` (SDK `mcp` 2.x, classe `MCPServer`). Cada tool é uma casca fina
sobre `services/`; erros de domínio viram `ToolError` com mensagem em português.

## Conectar o Claude Code CLI
```bash
claude mcp add --transport http canais http://localhost:8123/mcp \
  --header "Authorization: Bearer <seu token>"
```
Em produção (Fase 9) acrescente também os headers do Service Token do Cloudflare Access
(`CF-Access-Client-Id` / `CF-Access-Client-Secret`) e adicione o domínio em `MCP_ALLOWED_HOSTS`.
Depois: `/mcp` mostra o servidor; diga "processa as pendências" ou use o prompt `/mcp__canais__process_pending`.

## Fluxo
`list_pending_tasks` → `claim_next_task` (reserva 30 min e devolve contexto + `how_to`) → executar →
ferramenta de escrita → `complete_task` (ou `fail_task` / `release_task`). A aprovação do roteiro é humana, no site.

## Tools implementadas (Fase 2)
| Tool | Descrição |
|---|---|
| `list_channels()` | canais ativos |
| `get_channel(channel_id)` | idioma, template de roteiro, estilo visual, duração alvo… |
| `list_videos(channel_id, status?)` | vídeos do canal |
| `get_video(video_id)` | tema, metadados, etapas, versões do roteiro (sem texto das cenas) |
| `get_script(video_id, version?)` | cenas da versão (padrão: mais recente) |
| `list_pending_tasks()` | tarefas abertas (pending/in_progress) |
| `claim_next_task(type?, video_id?)` | reserva e devolve `{task, how_to, video, channel, previous_script}`; `{"task": null}` se vazio |
| `complete_task(task_id, result_summary)` | só quem reservou (ou reserva expirada) |
| `fail_task(task_id, reason)` | idem |
| `release_task(task_id)` | devolve à fila |
| `get_timeline(video_id)` | timeline atual (salva ou padrão) + tempos das cenas |
| `save_timeline(video_id, timeline, task_id?)` | NOVA versão da timeline (validada); ver `docs/render.md` |
| `save_scene_prompts(video_id, prompts, task_id?)` | prompts de imagem por cena (roteiro aprovado); `prompts=[{scene_index (0-based), image_prompt, video_prompt?, vertical?, key_scene?}]`; devolve `missing_scene_indexes` |
| `save_script(video_id, scenes, task_id?)` | nova versão do roteiro; `scenes=[{text, notes?}]`, 1–200 cenas |

Prompt: `process_pending`.

## Jobs (Fase 3)
| Tool | Descrição |
|---|---|
| `get_job_status(job_id)` | status, progresso, erro, resultado e fim do log |
| `list_jobs(video_id?, status?)` | jobs recentes |
| `get_metadata(video_id)` | título/descrição/tags atuais, problemas e última proposta (ver `docs/youtube.md`) |
| `save_metadata(video_id, title, description?, tags?, thumbnail_ideas?, task_id?)` | NOVA proposta de metadados do YouTube (a pessoa aplica no site; nada vai ao YouTube) |
| `get_shorts(video_id)` | seleção de Shorts, se foi dispensada e renders (etapa opcional; ver `docs/shorts.md`) |
| `save_short_selections(video_id, shorts, task_id?)` | NOVA seleção: `shorts=[{exact_text, title, hook_override?}]`; o servidor localiza o texto na narração e valida 15–59 s |
| `start_shorts(video_id, quality?, index?, allow_missing?)` | enfileira o render 9:16 (`preview` 720×1280 / `final` 1080×1920) e devolve `job_id` |
| `start_render(video_id, quality?, subtitles?, allow_missing?)` | enfileira o render (`preview` 720p / `final` 1080p) e devolve `job_id`; sem créditos, só CPU |
| `get_edit_package(video_id, quality?)` | manifesto da **edição livre** (cenas, tempos por palavra, links assinados de 3 h, `upload.url`); o Claude edita no sandbox e envia o MP4 em partes. Ver `docs/external-edit.md`; tipo de tarefa `edit_video` |

Narração/alinhamento só são disparados por pessoas no site (gastam créditos).

## Planejadas
Upload ao YouTube **não** é tool MCP (só site/REST). Regra: nenhuma tool bloqueia — operações longas criam Job e retornam `job_id`.

## Auth e extensão
`mcp/auth.py:BearerAuthMiddleware` valida o token e expõe o usuário em `mcp/context.py:current_user`.
OAuth (conectores do Claude Desktop) é extensão futura: o `MCPServer` aceita `auth=`/`token_verifier=`.


## Autenticação do `/mcp`
`Authorization: Bearer cd_...` (token de API) **ou** access token OAuth de conector (ver `docs/connect-claude.md`). Sem credencial: `401` com `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/mcp"`.
