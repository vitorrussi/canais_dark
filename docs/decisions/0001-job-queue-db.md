# 0001 — Fila de jobs no banco + worker separado
**Decisão:** jobs e ClaudeTasks vivem em tabelas SQLite; um processo worker os reserva atomicamente.
**Por quê:** 2 usuários e 1 servidor; evita Redis. Acesso via `services/jobs.py` permite trocar depois.
