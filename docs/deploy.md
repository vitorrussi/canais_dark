# Deploy / acesso externo

## Situação atual (teste)
- Domínio: `https://canaisdark.russi.app` → Cloudflare Tunnel (container `cloudflare-cloudflare-tunnel-1`, pasta `~/Projetos/cloudflare`, rede `host`,
  token gerenciado no painel). A rota **canaisdark.russi.app → http://localhost:8123** está configurada no painel do Cloudflare Zero Trust.
- Subir o sistema para o túnel: `make serve` (uvicorn em 127.0.0.1:8123, sem reload, `--proxy-headers`). Para desenvolver com reload: `make dev`.
- Jobs (narração etc.): **`make worker`** em outro processo (hoje iniciado manualmente, como o `make serve`).
- Ligar/desligar o túnel: `docker start|stop cloudflare-cloudflare-tunnel-1` (política `unless-stopped`).

## Variáveis (`.env`)
| Variável | Para quê |
|---|---|
| `PUBLIC_URL=https://canaisdark.russi.app` | Ativa o modo exposto: exige `SECRET_KEY` real, cookie de sessão `Secure`, host liberado no `/mcp` e URL correta na página Conectar Claude |
| `SECRET_KEY` | Assina o cookie de sessão. **Obrigatório** com `PUBLIC_URL` (o app não sobe com o valor padrão). Trocar invalida todas as sessões |
| `MCP_ALLOWED_HOSTS` | Hosts extras aceitos no `/mcp` (o de `PUBLIC_URL` entra sozinho) |
| `ENV=prod` | Logs em JSON |

## Segurança aplicada
- Login só com token de API; limite de **10 falhas por IP em 10 min** (usa `CF-Connecting-IP`), depois 429.
- `/api` e `/mcp` exigem `Authorization: Bearer`; sessão do site em cookie assinado, HttpOnly, SameSite=Lax, Secure.
- Proteção DNS-rebinding do SDK MCP ativa (só `localhost` e o host público).
- **Recomendado (Fase 9):** Cloudflare Access na frente do site (login por e-mail) e Service Token no `/mcp`. Hoje o hostname está aberto: quem souber o endereço vê a tela de login.

## Claude Code pelo domínio
```bash
claude mcp add --transport http canais https://canaisdark.russi.app/mcp --header "Authorization: Bearer <token>"
```
(`/connect` no site já mostra esse comando com a URL certa.) Se ativar Cloudflare Access no `/mcp`, acrescente os headers `CF-Access-Client-Id` e `CF-Access-Client-Secret`.

## Pendente (Fase 9)
Serviço persistente (systemd/Docker Compose) para o app, backup automático do banco e dos arquivos, regras do Access, limite de upload (Cloudflare: 100 MB/requisição).
