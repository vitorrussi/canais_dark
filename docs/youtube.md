# YouTube: metadados e upload privado (Fase 7)

O sistema envia o vídeo aprovado (e, se você quiser, os Shorts) ao YouTube **sempre como PRIVADO**. Publicar, ajustes
finais (conteúdo alterado/sintético, vídeo relacionado dos Shorts, teste A/B) e agendamento são manuais no Studio.
O envio só acontece quando uma pessoa clica no site (ou chama a REST): **nenhuma tool MCP envia ao YouTube**.

## Fluxo no site (página do vídeo, cartão ⑥)
1. Metadados: o Claude propõe (`save_metadata`, tarefa `generate_metadata` — só quando você pede) e você **aplica**, ou
   edita direto. Limites: título ≤ 100 e sem `< >`; descrição ≤ 5000 bytes; tags ≤ 500 caracteres no total.
2. Thumbnail (opcional): você envia JPG/PNG (16:9, 1280×720; ≤ 2 MB, recomprime se passar).
3. Enviar: exige canal conectado, render **final aprovado** (cartão ④), não parcial e atualizado, título e descrição.
   Shorts: precisam do render final (1080×1920) do cartão ⑤. Reenviar o mesmo arquivo pede confirmação (duplicata).
4. Resultado: link `youtu.be/…` e atalho para o Studio; `Video.youtube_url` guarda o do vídeo longo.

## Cota
`videos.insert` custa 1600 unidades, `thumbnails.set` 50; a cota padrão do Google é 10.000/dia (≈ 6 envios), por
**projeto** Google e zera à meia-noite do Pacífico. O sistema soma os envios do dia (`YOUTUBE_DAILY_QUOTA`) e bloqueia
antes de gastar. Se você pedir aumento ao Google, ajuste a variável.

## Configurar o Google (uma vez)
1. https://console.cloud.google.com → crie um projeto (ex.: "Estúdio Canais Dark").
2. **APIs e serviços → Biblioteca → YouTube Data API v3 → Ativar**.
3. **Tela de consentimento OAuth**: tipo *Externo*; nome do app; seu e-mail; escopos
   `.../auth/youtube.upload` e `.../auth/youtube.readonly`; adicione seu e-mail como *usuário de teste*.
4. **Credenciais → Criar credenciais → ID do cliente OAuth → Aplicativo da Web**.
   URIs de redirecionamento autorizados: `https://<seu-domínio>/oauth/youtube/callback`
   (e, para testar localmente, `http://localhost:8123/oauth/youtube/callback`). O endereço precisa bater exatamente
   com `PUBLIC_URL` (ou com o endereço que você abre no navegador, em dev).
5. No `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` e `FERNET_KEY`
   (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Reinicie servidor e worker.
6. **Importante:** com a tela de consentimento em modo *Teste*, o Google expira o refresh token em **7 dias**.
   Em *Tela de consentimento → Publicar app* (uso próprio; aparece o aviso "app não verificado", é normal) o token não expira.
7. Na página do canal → Configurações → **YouTube**: *Conectar YouTube*, escolha a conta/canal de marca certos no
   Google e confira o nome mostrado depois.

Sem credenciais, `YOUTUBE_FAKE=true` simula tudo (conectar, enviar) sem rede, para experimentar a interface.

## Segurança
- Refresh token criptografado (Fernet) na tabela `youtube_account`; nunca aparece em tela, log, exportação ou MCP.
- `state` do OAuth assinado, expira em 10 min e é amarrado ao usuário que iniciou.
- Projetos Google novos e não auditados têm uploads forçados a privado pelo próprio YouTube (alinha com esta regra).
- `invalid_grant` marca a conexão como "reconectar".

## Código
`integrations/youtube.py` (cliente real + `FakeYouTube`), `services/youtube.py` (conexão, cota, `prepare/start_upload/run_job`),
`services/metadata.py`, `services/thumbnail.py`, `worker/handlers.py:run_youtube_upload`, `web/youtube.py`,
`api/youtube.py`, `api/metadata.py`. Dados: `youtube_account`; ativos `metadata`, `thumbnail`, `upload`.
