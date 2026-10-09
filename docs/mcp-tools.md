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
| `save_script(video_id, scenes, task_id?)` | nova versão do roteiro; `scenes=[{text, notes?}]`, 1–200 cenas |

Prompt: `process_pending`.

## Planejadas
`save_scene_prompts`, `save_timeline`, `save_metadata`, `save_short_selections` (Fases 4–6);
`get_job_status`, `start_render`, `start_shorts` (Fases 3, 5, 6). Regra: nenhuma tool bloqueia — operações longas criam Job e retornam `job_id`.

## Auth e extensão
`mcp/auth.py:BearerAuthMiddleware` valida o token e expõe o usuário em `mcp/context.py:current_user`.
OAuth (conectores do Claude Desktop) é extensão futura: o `MCPServer` aceita `auth=`/`token_verifier=`.
