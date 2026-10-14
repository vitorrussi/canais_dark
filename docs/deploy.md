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
- `/mcp` aceita o token `cd_...` e OAuth de conector (Claude Desktop/claude.ai). `/register`, `/authorize`, `/token`, `/revoke` e `/.well-known/*` ficam públicos (padrão OAuth); `/x/*` (links assinados da edição livre, `docs/external-edit.md`) também precisa passar pelo WAF e pelo Access; se ativar Cloudflare Access, **não** proteja esses caminhos nem `/mcp` com login por e-mail (use bypass/Service Token), senão o conector não consegue autenticar.
- **Recomendado (Fase 9):** Cloudflare Access na frente do site (login por e-mail) e Service Token no `/mcp`. Hoje o hostname está aberto: quem souber o endereço vê a tela de login.

## Claude Code pelo domínio
```bash
claude mcp add --transport http canais https://canaisdark.russi.app/mcp --header "Authorization: Bearer <token>"
```
(`/connect` no site já mostra esse comando com a URL certa.) Se ativar Cloudflare Access no `/mcp`, acrescente os headers `CF-Access-Client-Id` e `CF-Access-Client-Secret`.

## Mover para o servidor (scripts em `deploy/`)
1. **Origem:** `deploy/pack.sh` gera `canaisdark-bundle-*.tar.gz` (`.env` + cópia consistente do banco + `data/files`; contém segredos).
2. Envie por `scp` e, no servidor: `git clone ... && cd canais_dark && deploy/install.sh --bundle ~/canaisdark-bundle-*.tar.gz`.
3. `install.sh` (idempotente; reexecute após `git pull`): confere git/uv/ffmpeg/systemd, `uv sync --frozen`, restaura o pacote
   (nunca sobrescreve sem `--force`; o antigo vira `.bak-<data>`), faz cópia do banco em `data/backups/`, roda migrações, instala e
   inicia `canaisdark-web` e `canaisdark-worker` (systemd, `Restart=always`) e testa `127.0.0.1:8123`. `--dry-run` só mostra o que faria.
4. Manual: subir o túnel do Cloudflare no servidor com o MESMO token e desligar o do PC antigo. Manter a mesma `FERNET_KEY`.

## Pendente (Fase 9)
Backup automático do banco e dos arquivos, regras do Access, limite de upload (Cloudflare: 100 MB/requisição).
