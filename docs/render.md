# Timeline e render (Fase 5)

Transforma narração + tempos + imagens/clipes em um vídeo 16:9 com ffmpeg. Tudo roda no **worker**
(`make worker`), nunca dentro de uma requisição. Sem custo de API: só CPU local.

## Pré-requisitos
Roteiro aprovado → narração com tempos por cena (forçados ou estimados) → imagem/clipe `main` em cada cena
(ou "preencher com fundo escuro" para testes). `ffmpeg`/`ffprobe` instalados (`sudo apt install ffmpeg`).
Narração de teste curto renderiza só as cenas já narradas (marcado "só teste").

## Timeline (JSON versionado)
Schema em `schemas/timeline.py` (`version: 1`). Guarda só decisões de edição; os tempos das cenas vêm do alinhamento.
- por cena: `scene_index` (0-based), `asset_id?` (padrão = imagem/clipe `main`), `motion` {`kind`: zoom_in, zoom_out,
  pan_left, pan_right, pan_up, pan_down, static; `intensity` 0–0,3}, `transition_in` {`cut`|`crossfade`, `duration` 0–2 s,
  vem da cena anterior), `texts` (até 4; `start_s`/`end_s` relativos à cena; `position` top|center|bottom);
- globais: `fade_in_s`, `fade_out_s`, `subtitles.enabled`, `music` {`asset_id`, `volume_db`, `ducking`}, `effects` {`vignette`, `grain`}.

`services/timeline.py`: `collect_timing` (cada cena fica na tela do seu início até o início da seguinte; a última até o fim
do áudio + 0,8 s), `build_default` (movimento determinístico sem repetir em cenas vizinhas, crossfade 0,6 s/0,3 s, corte em
cenas < 1,5 s; com `base` preserva escolhas existentes), `validate_for_video`, `save` (SEMPRE versão nova, ativo `timeline`
com `source` auto|user|claude e `inputs_hash`), `latest`, `inputs_hash` (muda com áudio/tempos/imagens → "desatualizada").

## Como o render funciona (`services/render.py`)
1. **Plano** (`plan`): fontes, movimentos, transições efetivas (`clamp_transitions`: as duas metades de transição dentro de
   uma cena somam ≤ 90%), quadros por segmento (`frame_layout`/`build_segments`).
2. **Segmentos por cena**: cada segmento é um MP4 sem áudio; o fim dele já contém o crossfade para a cena seguinte (`xfade`
   entre as duas imagens) e a cena seguinte continua de onde o crossfade terminou — a concatenação `-c copy` é contínua.
   Ken Burns com `zoompan` sobre imagem ampliada 2× (sem tremida); clipes ajustados ao quadro e repetidos se curtos.
3. **Cache**: nome do arquivo = hash (spec da cena + próxima cena + qualidade + carimbo dos arquivos). Só o que mudou
   é refeito; cancelar/tentar de novo retoma. Segmentos fora do plano são apagados.
4. **Paralelo**: até 6 ffmpeg ao mesmo tempo (metade dos núcleos); cancelar mata os processos em andamento.
5. **Passo final**: concat + áudio (narração; trilha em loop com volume, ducking por `sidechaincompress` e fade-out de 2 s)
   + legendas `.ass` e textos na tela (`ass`). Sem legendas/textos o vídeo não é recodificado (`-c:v copy`).
6. **Resultado**: ativo `render` (`kind` preview|final); só o último de cada tipo é mantido.

Qualidades: **prévia** 1280×720@24 `veryfast` crf 26; **final** 1920×1080@30 `fast` crf 18.
Medido (vídeo #4, 3 cenas, 16 s): prévia ~7 s, final ~12 s (12 núcleos).

## Etapas do pipeline
`timeline`: pendente/em andamento (tarefa do Claude), concluída, **desatualizada** (áudio/tempos/imagens mudaram).
`render`: na fila/rodando, concluída (final atual), parcial (só prévia ou teste curto), desatualizada, falhou.
`review`: pendente quando há final atual; concluída ao clicar **Aprovar vídeo** (grava quem e quando em `meta`).

## Trilha sonora (`services/music.py`)
Ativo `music` por vídeo; upload validado pela assinatura (MP3/WAV/OGG/M4A/FLAC), ≤ `MAX_UPLOAD_MB`; dá para reaproveitar
a de outro vídeo do canal (cópia). Selecionar/ajustar cria nova versão da timeline.

## Superfícies
- Site: cartão **④ Timeline e render** em `/videos/{id}` (player, baixar, aprovar, trilha, legendas) e editor em
  `/videos/{id}/timeline` (movimento, transição, texto por cena, JSON avançado). Rotas em `web/render.py`.
- REST: `GET/PUT /api/videos/{id}/timeline`, `POST/GET /api/videos/{id}/render`.
- MCP: `get_timeline`, `save_timeline`, `start_render` (retorna `job_id`; acompanhe com `get_job_status`) e tarefa
  `generate_timeline` (contexto: cenas com tempos/asset, `default_timeline`, regras em `how_to`).

## Limites e cuidados
- O render ocupa o worker (um job por vez): narração enfileirada espera.
- Imagens 1376×768 ficam macias em 1080p (ampliadas); imagens ≥ 1920×1080 resolvem.
- Overlays tipo névoa/poeira, editor visual com arrastar e aceleração por GPU (`h264_nvenc`) ficam como extensão.
- Mudou a forma de montar segmentos? Incremente `ALGO_VERSION` em `services/render.py` (invalida o cache).
