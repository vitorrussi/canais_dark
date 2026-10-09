# Shorts 9:16 (Fase 6) — etapa OPCIONAL

Gera Shorts verticais a partir de trechos da narração do vídeo longo. **Nada depende dos Shorts**: revisão,
metadados e uploads seguem sem eles. A pessoa pode **dispensar** a etapa ("Não vou fazer Shorts deste vídeo"; reversível).
Sem custo de API: só CPU/ffmpeg no worker.

## Fluxo
1. Seleção: o Claude (tarefa `select_shorts`, só quando a pessoa pede) ou a pessoa (colar o trecho no site) escolhe
   2–3 trechos de 25–55 s (limites duros 15–59 s, até 6 por vídeo).
2. O servidor **localiza** o texto exato no alinhamento (`services/shorts.py:locate`, tolerante a caixa/pontuação,
   confiança ≥ 90%), aplica folga (−0,15 s/+0,35 s) e valida duração e sobreposição. Erros vêm numa lista única.
3. Render (`job shorts`): por Short, segmentos 9:16 reaproveitando `services/render.py` + passo final com fatia da narração.

## Modelo de dados (sem migração)
- `Asset(type="shorts_plan")`: JSON versionado (`schemas/shorts.py:ShortsPlan`: `skipped`, `timing`, `shorts[]` com
  `title`, `exact_text`, `hook_override`, `start_s`, `end_s`, `source`, `located`, `confidence`). Nova versão a cada edição.
- `Asset(type="short")`: um mp4 por (índice, `kind` preview|final); `meta`: index, title, start_s, end_s, duration, width, height, plan_asset_id.
  Renders novos substituem o anterior do mesmo (índice, kind).

## Montagem
- Qualidades: prévia 720×1280@24, final 1080×1920@30 (`render.SHORT_QUALITIES`).
- Layout por cena: `fill` quando existe imagem/clipe da variante **vertical**; `blur` com a imagem 16:9 em largura total sobre
  o próprio fundo desfocado e escurecido (`_blur_chain`). Movimento vem da timeline do vídeo (padrão: zoom suave).
- Áudio: fatia da narração (`RenderPlan.audio_start`, fades de 0,15/0,25 s) + trilha da timeline com ducking, se houver.
- Legendas: `subtitles.to_ass_shorts` (ASS 1080×1920, 2–3 palavras por vez, fonte 104, palavra falada em amarelo,
  terço inferior fora da zona da interface). Gancho (`hook_override`) no topo nos primeiros 2,5 s.
- Cache de segmentos por hash (como o render longo). `ALGO_VERSION` = 2.

## Superfícies
- Site: cartão **⑤ Shorts (opcional)** em `/videos/{id}` (recolhido até haver algo): adicionar trecho, pedir ao Claude,
  prévia/final por Short ou todos, player 9:16, baixar, dispensar/reativar. Código: `web/shorts.py`, `_shorts_card.html`.
- MCP: `get_shorts`, `save_short_selections(video_id, shorts, task_id?)`, `start_shorts(video_id, quality?, index?, allow_missing?)`.
- REST: `GET/PUT /api/videos/{id}/shorts`, `POST /api/videos/{id}/shorts/render`, `POST /api/videos/{id}/shorts/skip`.
- Etapa `shorts` em `video_status`: não iniciado → pendente (selecionado) → parcial/concluído; `skipped` = dispensada; `stale` se o roteiro aprovado mudou.

## Limitações
- Sem Forced Alignment os tempos são estimados: o corte pode cair fora da palavra (aviso no cartão).
- Imagens 1376×768 em `blur` ocupam 1080 px de largura (um pouco macias).
- Upload dos Shorts é da Fase 7.
