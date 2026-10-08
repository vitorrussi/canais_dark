# Projeto: Estúdio de Canais Dark (multicanal, com Claude via MCP)

> Este documento é o briefing inicial do projeto. Leia-o inteiro antes de planejar.
> Trabalharemos **em fases, uma de cada vez**. Em cada fase: planeje (plan mode), espere minha aprovação, implemente, teste, e só então avance.

---

## 1. Objetivo

Um sistema web para gerenciar **vários canais dark do YouTube** (canais sem rosto) e o **pipeline de produção de cada vídeo**: roteiro → narração → imagens → edição/render → Shorts → upload.

O sistema roda num **computador servidor em casa**, acessado de fora da rede local via **Cloudflare Tunnel** por **duas pessoas** (eu e um sócio), cada uma no seu próprio computador.

O trabalho criativo (roteiro, prompts de imagem, decisões de edição, metadados) é feito pelo **Claude via MCP** (Claude Code CLI ou Claude Desktop, usando a assinatura de cada pessoa). **A API do Claude não será usada em nenhum momento.** O servidor guarda o estado e executa o trabalho pesado; o Claude é um "trabalhador" acionado manualmente por nós.

---

## 2. Restrições e decisões já tomadas

- **Sem API do Claude.** Toda interação com o Claude acontece via **servidor MCP** exposto pelo próprio backend.
- **MCP via Streamable HTTP**, montado no mesmo app em `/mcp`. Webhooks não fazem parte da arquitetura (o Claude não recebe webhooks; ele age quando acionado).
- **Fila de tarefas para o Claude:** o site cria tarefas pendentes (ex.: "gerar roteiro do vídeo 42"); no Claude dizemos "processa as pendências", e ele usa as ferramentas MCP para pegar, executar e salvar. Ao pegar uma tarefa, ela é **reservada** (lock com quem pegou e quando) para que duas pessoas não processem a mesma tarefa.
- **Nenhuma ferramenta MCP pode bloquear por muito tempo.** O proxy da Cloudflare corta respostas após ~100 s. Operações longas (TTS, alinhamento, render, upload) **criam um job e retornam `job_id` imediatamente**; existe uma ferramenta `get_job_status(job_id)`.
- **Acesso externo:** Cloudflare Tunnel (já uso em outros serviços).
  - Site (`/`, `/api`): protegido por Cloudflare Access (login por e-mail).
  - `/mcp`: Service Token do Cloudflare Access para o Claude Code CLI. Além disso, o próprio app deve exigir um **token por usuário** (Bearer) no MCP e na API, para saber quem fez o quê. Deixar o design pronto para adicionar OAuth no MCP depois (para conectores do Claude Desktop), mas **não implementar OAuth agora**.
- **Imagens/vídeos de IA são gerados manualmente** por nós (ChatGPT, Gemini etc.). O sistema **não automatiza a interface web do ChatGPT** (viola os termos de uso). O sistema gera os prompts e recebe os arquivos por upload.
- **Sem custos extras de API além do necessário:** ElevenLabs usa os créditos do plano; YouTube Data API e Google Drive API são gratuitas (com cota).
- **Armazenamento de trabalho local** em `DATA_DIR/{channel_slug}/{video_id}/...`. O **Google Drive é cópia/arquivo e compartilhamento** (sincronizado por job), não a área de trabalho.
- **Segredos** (chaves ElevenLabs, credenciais Google, tokens) apenas em `.env` / variáveis de ambiente. Nunca commitar.

---

## 3. Stack

- **Python 3.12+**
- **FastAPI** (backend + API REST em `/api`)
- **FastMCP** (ou o SDK oficial `mcp` em Python) montado em `/mcp` com transporte Streamable HTTP, compartilhando a mesma camada de serviços do REST
- **SQLModel/SQLAlchemy + SQLite** (com Alembic para migrações; manter compatível com Postgres no futuro)
- **Fila de jobs:** prefira algo simples. Proponha entre (a) fila no próprio banco + processo worker separado, ou (b) RQ/arq com Redis. Justifique a escolha.
- **ffmpeg** para render (chamado via subprocess; nada de render dentro de request HTTP)
- **Frontend:** proponha entre Jinja2 + HTMX (mais simples, tudo no mesmo app) ou React separado. Critério: simplicidade e velocidade de entrega para uso interno.
- **pytest** para testes; type hints em tudo; logging estruturado.

**Princípio de arquitetura:** a lógica vive numa **camada de serviços** (biblioteca Python). REST, MCP, workers e uma pequena CLI de administração são **cascas finas** sobre essa camada. Tudo deve ser testável sem o Claude.

---

## 4. Modelo de dados (ponto de partida — ajuste se necessário)

- **User:** nome, e-mail, token de API (hash).
- **Channel:** nome, slug, descrição, link do YouTube, idioma principal (+ idiomas extras), `elevenlabs_voice_id`, **prompt de estilo visual** (prefixo fixo para todos os prompts de imagem do canal), template/tom de roteiro, duração alvo, pasta do Drive, credenciais/refresh token do YouTube (criptografadas).
- **Video:** canal, tema/conteúdo de entrada, status geral, título, descrição, tags, idioma, links (Drive, YouTube), timestamps.
- **ScriptVersion:** vídeo, número da versão, texto completo, aprovado (bool), autor (usuário/Claude).
- **Scene:** vídeo, versão do roteiro, índice, texto narrado, prompt de imagem, prompt de vídeo (opcional), asset associado, `start`/`end` (preenchidos pelo alinhamento), flag "candidata a Short", status.
- **Asset:** vídeo, cena (opcional), tipo (audio, alignment, image, video_clip, subtitle, render, short, thumbnail), caminho local, `drive_file_id`, metadados (resolução, duração, hash).
- **Timeline:** vídeo, JSON da linha do tempo de edição (gerado pelo Claude), versão.
- **Job:** tipo (tts, alignment, render, shorts, youtube_upload, drive_sync…), vídeo, status, progresso, log, erro, tentativas, timestamps.
- **ClaudeTask:** tipo (gerar_roteiro, gerar_prompts, gerar_timeline, gerar_metadados, selecionar_shorts…), vídeo, instruções extras, status (pending, in_progress, done, failed), `claimed_by`, `claimed_at`, resultado.

**Invalidação:** cada etapa guarda de qual versão da etapa anterior ela depende. Se o roteiro mudar após a narração, áudio/alinhamento/timeline ficam marcados como **desatualizados** e o site mostra isso.

---

## 5. Pipeline de um vídeo

1. Criar vídeo no canal com o **conteúdo/tema de entrada**.
2. Site cria `ClaudeTask(gerar_roteiro)`. O Claude gera o roteiro **já dividido em cenas** e salva via MCP.
3. **Aprovação humana do roteiro** no site (antes de gastar créditos do ElevenLabs).
4. Em paralelo:
   - **4a.** Botão "Gerar narração" → job TTS no ElevenLabs (voz do canal) → em seguida job de **Forced Alignment** (`POST /v1/forced-alignment` com áudio + texto) → salva `alignment.json` (tempo por palavra) e gera `.srt`/`.ass`. Preencher `start`/`end` de cada cena. Mostrar palavras com `loss` alto para revisão.
   - **4b.** `ClaudeTask(gerar_prompts)` → o Claude escreve prompt de imagem por cena (usando o prompt de estilo do canal; indicar cenas que merecem versão 9:16 e cenas-chave para vídeo de IA). O site exibe um **pacote de prompts** com botão de copiar por cena.
5. Upload manual das imagens/clipes **por cena** (um slot por cena, arrastar e soltar; validar formato/resolução; aceitar também upload em lote que preenche as cenas pendentes em ordem).
6. `ClaudeTask(gerar_timeline)` → o Claude produz a **linha do tempo da edição em JSON** (asset por cena, movimento tipo zoom/pan, textos na tela, transições, trilha/volume). O schema deve ser validado com Pydantic.
7. Job de **render** (ffmpeg): áudio + timeline + alinhamento → vídeo 16:9 com movimento nas imagens e legendas opcionais.
8. **Revisão humana** do vídeo no site (player).
9. `ClaudeTask(gerar_metadados)` → título, descrição (com capítulos a partir das cenas), tags, ideias de thumbnail.
10. `ClaudeTask(selecionar_shorts)` → 2–3 trechos (texto exato) → job que localiza o trecho no `alignment.json`, monta em 9:16 a partir dos assets das cenas (fundo desfocado quando a imagem for 16:9) e queima legendas grandes.
11. Job de **upload no YouTube** como **privado** (título, descrição, tags, thumbnail). Ajustes finais (conteúdo alterado, vídeo relacionado dos Shorts, teste A/B) são feitos manualmente no Studio.
12. Job de **sincronização com o Google Drive**; links salvos no banco e exibidos no site.

---

## 6. Ferramentas MCP (ponto de partida)

Leitura:
- `list_channels()`, `get_channel(channel_id)`
- `list_videos(channel_id, status?)`, `get_video(video_id)` (inclui roteiro aprovado, cenas, status das etapas)
- `list_pending_tasks()`, `get_job_status(job_id)`

Tarefas:
- `claim_next_task(type?)` → reserva e retorna a próxima tarefa
- `complete_task(task_id, result_summary)`, `fail_task(task_id, reason)`

Escrita (validadas por schema):
- `save_script(video_id, scenes: list[{text, notes?}])` → cria nova ScriptVersion
- `save_scene_prompts(video_id, prompts: list[{scene_index, image_prompt, video_prompt?, vertical?, key_scene?}])`
- `save_timeline(video_id, timeline_json)`
- `save_metadata(video_id, title, description, tags, thumbnail_ideas)`
- `save_short_selections(video_id, shorts: list[{exact_text, hook_override?, title}])`

Ações (criam jobs e retornam `job_id`):
- `start_render(video_id)`, `start_shorts(video_id)`

Regras: ferramentas com descrições claras e exemplos; erros explicativos (ex.: "roteiro ainda não aprovado"); nada que bloqueie mais que alguns segundos.

---

## 7. Fases de implementação

Implemente **uma fase por vez**. Cada fase termina com testes passando e um resumo do que foi feito e de como testar manualmente.

- **Fase 0 — Fundação:** estrutura do projeto, configuração via `.env`, banco + Alembic, camada de serviços, healthcheck, usuários com token de API, logging, pytest, README, `CLAUDE.md` com as convenções do projeto.
- **Fase 1 — Canais e vídeos:** CRUD de canais e vídeos (REST + UI básica), página do vídeo mostrando o status de cada etapa.
- **Fase 2 — MCP + tarefas do Claude:** servidor MCP em `/mcp` (Streamable HTTP) com autenticação por token, fila de `ClaudeTask` com reserva, ferramentas de leitura e de roteiro/cenas; aprovação do roteiro no site. Testar conectando o Claude Code CLI localmente.
- **Fase 3 — Jobs + ElevenLabs:** fila de jobs e worker; TTS; Forced Alignment; geração de `.srt`/`.ass`; preenchimento dos tempos das cenas; invalidação quando o roteiro muda.
- **Fase 4 — Prompts e assets:** pacote de prompts no site; upload de imagens/clipes por cena e em lote; validação; versões 9:16.
- **Fase 5 — Render:** schema da timeline; worker de render com ffmpeg (Ken Burns, transições simples, legendas, trilha com ducking simples); player no site.
- **Fase 6 — Shorts:** seleção pelo Claude, localização no alinhamento, montagem 9:16, legendas grandes.
- **Fase 7 — YouTube:** OAuth por canal, upload privado com metadados e thumbnail, respeitando cotas.
- **Fase 8 — Google Drive:** sincronização por job, estrutura de pastas por canal/vídeo, links no site.
- **Fase 9 — Deploy:** serviço no servidor (systemd ou Docker Compose), Cloudflare Tunnel, regras do Access (site com login, `/mcp` com Service Token), backup automático do banco e dos arquivos.

Fora de escopo por enquanto: geração local de imagens (ComfyUI), dublagem/segunda faixa de idioma, OAuth para o MCP, analytics do YouTube. Deixe pontos de extensão, mas não implemente.

---

## 8. Como quero que você trabalhe

1. **Agora (plan mode):** leia este documento e devolva:
   - a arquitetura proposta (estrutura de pastas, módulos, como REST/MCP/worker compartilham a camada de serviços);
   - as escolhas em aberto (fila de jobs, frontend, SDK MCP), com recomendação e justificativa;
   - dúvidas ou riscos que você enxerga neste briefing;
   - o **plano detalhado apenas da Fase 0**.
   Não escreva código ainda. Espere minha aprovação.
2. Depois de aprovado, implemente a fase, rode os testes e me mostre como verificar.
3. Antes de cada nova fase, faça o plano dela e espere aprovação.
4. Mantenha o `CLAUDE.md` atualizado com convenções, comandos e decisões, para que sessões futuras retomem o contexto.
5. Prefira soluções simples e explícitas. Se algo neste briefing parecer errado ou excessivo, diga antes de implementar.