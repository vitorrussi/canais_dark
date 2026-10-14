# Conectar o Claude sem terminal

Duas formas, independentes. Use a que fizer sentido para cada pessoa.

## 1. Conector por URL (Claude Desktop / claude.ai) — recomendado
Para quem não é técnico. Exige que a conta do Claude permita **conectores personalizados** (confira em
*Configurações → Conectores*).

1. **Administrador:** em `/admin/users` clique em **Link de acesso** na pessoa e envie o link por um canal privado
   (vale 7 dias; gerar novo token da pessoa invalida o link). Clicar nele já entra no site, sem colar token.
2. **A pessoa:** Claude Desktop → *Configurações → Conectores → Adicionar conector personalizado*; nome livre, endereço
   `https://<domínio>/mcp` (a página **Conectar Claude** mostra e copia o endereço).
3. Abre uma janela do navegador com **Permitir acesso?** → *Permitir*. Se pedir login, usar o link de acesso.
4. No chat: *"processe as tarefas pendentes do Estúdio de Canais Dark"*.

Desconectar: **Conectar Claude → Conectores autorizados → Desconectar** (vale na hora). O acesso dura 90 dias e se
renova sozinho enquanto o conector é usado; gerar um novo token da pessoa ou desativá-la derruba todos os conectores.

### Como funciona (resumo técnico)
- OAuth 2.1 com PKCE (S256) e registro dinâmico de cliente (`/register`), implementado sobre o SDK `mcp`
  (`mcp/oauth_routes.py`, `mcp/oauth_provider.py`, `services/oauth.py`).
- Descoberta: `/.well-known/oauth-authorization-server` e `/.well-known/oauth-protected-resource/mcp`; o `/mcp` responde
  `401` com `WWW-Authenticate: Bearer resource_metadata=...`.
- Tela "Permitir": `/oauth/consent` (exige sessão do site). Tokens opacos; só o SHA-256 vai ao banco
  (`oauth_client`, `oauth_token`). Access 1 h, refresh 90 dias com rotação, código de 5 min e uso único.
- O token de API `cd_...` continua valendo (Claude Code). O que o conector pode fazer é o mesmo das tools MCP:
  **nada aprova, renderiza ou envia a terceiros**.
- `/register` é público por padrão do OAuth; há teto de 300 clientes registrados.

## 2. Copiar e colar (sem MCP)
Funciona em qualquer conversa do Claude (inclusive plano grátis, celular ou navegador).

1. Na etapa do vídeo (roteiro, prompts, timeline, Shorts, metadados), clique em **Sem conectar o Claude? Copiar/colar**.
2. **Copiar pedido** → cole no Claude e envie.
3. Copie a resposta inteira (o bloco `{ ... }`), cole na caixa e clique **Aplicar resposta**.

O site valida (mesmas regras das tools MCP), salva como **nova versão** e conclui a tarefa. Resposta inválida não salva
nada e a mensagem diz o campo com problema. Também há REST: `GET/POST /api/videos/{id}/claude/{tipo}`
(`generate_script`, `generate_prompts`, `generate_timeline`, `select_shorts`, `generate_metadata`).
Código: `services/offline_tasks.py`, `web/offline.py`, `api/offline.py`.

## Problemas comuns
- **O conector não aparece / "personalizado" indisponível:** o plano da conta pode não permitir; use o copiar/colar.
- **"Este pedido de autorização expirou":** o pedido vale 10 minutos; refaça *Conectar* no Claude.
- **O Claude pede login e não aceita o token:** use o *link de acesso* (o login é o do site, não do Claude).
- **Perdeu o acesso do conector depois de gerar novo token:** é proposital (rotacionar derruba conectores); autorize de novo.
