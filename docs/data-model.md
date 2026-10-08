# Modelo de dados

Implementado:
- **User** (F0): id, name, email único, token_hash, is_active, created_at.
- **Channel** (F1): name, slug único/imutável, description, youtube_url, language, extra_languages (JSON),
  elevenlabs_voice_id, visual_style_prompt, script_template, target_duration_minutes, drive_folder_id,
  is_archived, timestamps. Credenciais do YouTube: coluna criptografada só na Fase 7.
- **Video** (F1): channel_id, topic, status (string; `draft` por ora), title, description, tags (JSON),
  language (herda do canal), drive_url, youtube_url, created_by, timestamps.

- **ScriptVersion** (F2): video_id, number, full_text, approved/approved_by/approved_at, author (`claude:<uid>`|`user:<uid>`), task_id.
- **Scene** (F2): video_id, script_version_id, index, text, notes (prompts/tempos/assets nas Fases 3–4).
- **ClaudeTask** (F2): type, video_id, instructions, status (`pending|in_progress|done|failed|cancelled`), claimed_by/claimed_at, attempts, result_summary, error, created_by, finished_at.
- Video ganhou `approved_script_version_id`.

Planejado (ver `docs/briefing.md` §4): Asset, Timeline, Job.

## Decisões
- **Invalidação** (Fase 3): cada artefato guarda `based_on_version` da etapa anterior; `is_stale`
  é calculado comparando com a versão atual, sem flags manuais.
- **ClaudeTask/Job**: reserva atômica (`UPDATE ... WHERE status='pending'`) com `claimed_by`/`claimed_at`;
  reservas expiram (ex.: 30 min) para não travar tarefas abandonadas.
- Credenciais do YouTube criptografadas com Fernet (`FERNET_KEY`).
