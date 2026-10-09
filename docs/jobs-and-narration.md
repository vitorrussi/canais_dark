# Jobs e narração (Fase 3)

## Visão geral
`Job` = trabalho pesado numa tabela do banco, executado por um **processo separado** (`make worker` → `canaisdark worker`).
Tipos: `tts` (gera o áudio) e `alignment` (mede o tempo de cada palavra). Estados: `queued → running → done | failed | cancelled`.
O worker reserva jobs de forma atômica, grava *heartbeat* e log; jobs `running` sem heartbeat por 10 min viram `failed` (podem ser repetidos).

**Precisa de dois processos rodando:** `make serve` (site) e `make worker` (jobs). Sem worker os jobs ficam “na fila”.

## Fluxo da narração (sempre disparado por uma pessoa, no card “Narração” do vídeo)
1. Roteiro **aprovado** (obrigatório) e `voice_id` no canal; `ELEVENLABS_API_KEY` no `.env`.
2. O site mostra a **prévia**: caracteres, créditos estimados (Flash/Turbo ≈ 0,5/caractere; demais 1), saldo, limite por geração (`ELEVENLABS_MAX_CHARS_PER_JOB`, padrão 2500).
3. **Teste curto** (padrão): só as primeiras N cenas → áudio marcado “só teste”. **Completa**: todas as cenas.
4. Job `tts`: divide em trechos ≤ `TTS_CHUNK_CHARS` (nunca no meio de uma cena, salvo cena gigante), 1 requisição por trecho com `previous_text/next_text`,
   junta (ffmpeg só se > 1 trecho) e grava `narration_v<roteiro>_<job>.mp3`. Trechos já gerados ficam em `audio/parts/` e são **reaproveitados** ao “Tentar de novo”.
5. **Forced Alignment (opcional)**: se ligado, enfileira o job `alignment` (áudio + texto → palavras com `start/end/loss`), mapeia palavras → cenas, preenche `Scene.start_s/end_s`
   e gera `alignment.json`, `.srt`, `.ass`. Se desligado, os tempos e legendas são **estimados** pela duração do áudio (proporcional ao tamanho das cenas).

## Forced Alignment configurável (3 níveis)
| Nível | Onde | Efeito |
|---|---|---|
| Global | `FORCED_ALIGNMENT_ENABLED=false` no `.env` | nunca chama a API de alinhamento |
| Canal | campo “Usar Forced Alignment” nas configurações do canal | padrão do canal |
| Geração | seletor no card (canal / ligar / desligar) | decide aquela narração |

## Custos e segurança de créditos
Teto por geração, teste curto, estimativa + saldo antes de gerar (botão pede confirmação), log com créditos gastos (saldo antes/depois). O custo do Forced Alignment não é documentado pelo ElevenLabs: meça no primeiro teste pelo log do job.
Plano grátis: 10.000 créditos/mês, **sem licença comercial**; vozes da Voice Library podem não funcionar na API (use *premade*); VPN/proxy pode bloquear a conta.

## Modo demonstração
`ELEVENLABS_FAKE=true` usa um ElevenLabs falso (sem chave, sem créditos; áudio sintético e mudo) para testar toda a interface e o worker.

## Cancelar e desfazer (importante para créditos)
O ElevenLabs **não tem API para cancelar** uma geração em andamento: depois que a requisição começa, o trecho é processado e pode ser cobrado. O sistema reduz o prejuízo assim:
1. **Janela de desfazer** (`NARRATION_UNDO_SECONDS`, padrão 10 s): ao clicar em *Gerar narração* o job só começa depois dela; *Desfazer* nesse período não gasta nada.
2. **Trechos menores** (`TTS_CHUNK_CHARS`, padrão 1500): mais pontos de cancelamento entre as requisições.
3. **Cancelar durante a geração**: o sistema usa o endpoint de *streaming* e, ao cancelar, fecha a conexão e para de esperar (o trecho em andamento pode já estar cobrado). Os trechos prontos ficam salvos e são reaproveitados numa nova tentativa.

## Escolha de voz e modelo
Configurações do canal → *Escolher voz e modelo* (`/channels/<slug>/voice`): modelos (Flash 0,5 / Turbo 0,5 / Multilingual v2 1 crédito por caractere), **Minhas vozes** e **Biblioteca (pt)** com **prévias gratuitas (▶)** e **teste curto com o seu texto** (≤150 caracteres, mostra o custo antes e pede confirmação).
No **plano gratuito** só as vozes da conta (*premade*) funcionam pela API; as da biblioteca aparecem com “Plano pago”. A lista carrega de forma assíncrona (a API do ElevenLabs leva até ~20 s; cache de 5 min).

## Invalidação
Narração/alinhamento ficam **Desatualizados** quando a versão aprovada do roteiro difere da usada (comparação automática; os arquivos antigos são mantidos). “Só teste” e “Pulada” são estados próprios do pipeline.

## Arquivos
`DATA_DIR/<canal>/<video>/audio/` — `narration_v<N>_<job>.mp3`, `…alignment.json`, `….srt`, `….ass`, `parts/`. Servidos por `/assets/<id>/file` (com `Range`; `?download=1` baixa).

## API / MCP
REST: `GET /api/jobs`, `/api/jobs/{id}`, `POST /api/jobs/{id}/cancel|retry`, `GET /api/videos/{id}/narration/plan`, `POST /api/videos/{id}/narration`.
MCP: `get_job_status(job_id)` e `list_jobs(video_id?, status?)` — **não existe tool para disparar TTS** (evita o Claude gastar créditos).
