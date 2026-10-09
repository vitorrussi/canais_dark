# Sincronização com o Google Drive (Fase 8) — etapa OPCIONAL

O Drive é **cópia/arquivo e compartilhamento**; o trabalho acontece no disco local (`DATA_DIR`). Nada depende desta etapa.
O envio só acontece quando uma pessoa clica em "Sincronizar" (ou chama a REST): **nenhuma tool MCP envia ao Drive**.

## Como funciona
- **Permissão mínima:** escopo `drive.file`. O sistema só enxerga o que ele mesmo criou; não lê o resto do seu Drive.
- **Pastas criadas pelo app:** `Estúdio de Canais Dark / <canal> / #<id> <título> / {video, shorts, audio, roteiro, thumbnail, midia}`.
  IDs ficam em `Channel.drive_folder_id` e `Video.drive_folder_id`; `Video.drive_url` guarda o link da pasta do vídeo.
  Se você apagar uma pasta no Drive, ela é recriada na próxima sincronização.
- **Conteúdo** (cartão ⑦ da página do vídeo): padrão = vídeo final, Shorts finais, thumbnail, roteiro (`.md` e `.json`),
  legendas (`.srt` e `.ass`) e narração (`.mp3`); **imagens/clipes/trilha** são opcionais a cada clique.
- **Só o que mudou:** cada arquivo tem uma chave estável; o registro (`Asset(type="drive_file")`) guarda o ID no Drive,
  o carimbo (tamanho+mtime ou versão do roteiro) e o SHA-256. Arquivo alterado → atualiza o MESMO arquivo no Drive
  (sem duplicar); inalterado → pula. Nunca apaga nada no Drive.
- **Falhas:** um arquivo com erro não impede os outros; o job termina "falhou" com o resumo e a nova tentativa envia só o que faltou.
  Drive sem espaço (`storage_full`), permissão e `invalid_grant` interrompem o resto na hora.
- **Estágio** `drive` (opcional): não iniciado → sincronizado → desatualizado (algum arquivo novo/alterado) → falhou.

## Configurar o Google (uma vez; reaproveita o projeto da Fase 7)
1. No mesmo projeto do Google Cloud: **APIs e serviços → Biblioteca → Google Drive API → Ativar**.
2. **Tela de consentimento OAuth**: adicione o escopo `.../auth/drive.file` (não sensível).
3. **Credenciais → seu ID do cliente OAuth**: adicione o redirecionamento
   `https://<seu-domínio>/oauth/drive/callback` (e `http://localhost:8123/oauth/drive/callback` para testar local).
4. `.env`: os mesmos `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` e `FERNET_KEY` da Fase 7. Reinicie servidor e worker.
5. Em modo *Teste* o refresh token expira em 7 dias; publique o app (uso próprio) para não expirar.
6. Canal → Configurações → **Google Drive** → *Conectar* e escolha a conta.
`DRIVE_FAKE=true` simula tudo sem Google, para ver a interface.

## Segurança
Refresh token criptografado (Fernet) em `drive_account`; `state` assinado (salt próprio, 10 min, amarrado ao usuário);
o token nunca aparece em tela, log, exportação ou MCP.

## Código
`integrations/drive.py` (cliente + `FakeDrive`), `services/drive.py` (OAuth, manifesto, sync, `run_job`),
`worker/handlers.py:run_drive_sync`, `web/drive.py`, `api/drive.py`.
