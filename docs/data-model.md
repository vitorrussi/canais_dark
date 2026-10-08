# Modelo de dados

Implementado na Fase 0: **User** (id, name, email único, token_hash, is_active, created_at).

Planejado (ver `docs/briefing.md` §4): Channel, Video, ScriptVersion, Scene, Asset, Timeline, Job, ClaudeTask.

## Decisões
- **Invalidação** (Fase 3): cada artefato guarda `based_on_version` da etapa anterior; `is_stale`
  é calculado comparando com a versão atual, sem flags manuais.
- **ClaudeTask/Job**: reserva atômica (`UPDATE ... WHERE status='pending'`) com `claimed_by`/`claimed_at`;
  reservas expiram (ex.: 30 min) para não travar tarefas abandonadas.
- Credenciais do YouTube criptografadas com Fernet (`FERNET_KEY`).
