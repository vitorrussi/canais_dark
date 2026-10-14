#!/usr/bin/env bash
# Instala/atualiza o Estúdio de Canais Dark neste servidor (systemd).
# Rode na raiz do clone, como seu usuário normal (usa sudo só para o systemd):
#   deploy/install.sh [--bundle pacote.tar.gz] [--force] [--dry-run]
# É idempotente: pode rodar de novo a cada atualização (git pull && deploy/install.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"

bundle=""; force=0; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --bundle) bundle="${2:?informe o arquivo}"; shift 2 ;;
    --force) force=1; shift ;;
    --dry-run) dry=1; shift ;;
    *) echo "uso: $0 [--bundle arq.tar.gz] [--force] [--dry-run]" >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { echo "ERRO: $*" >&2; exit 1; }
run() { if [ "$dry" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

say "Conferindo dependências"
for c in git uv ffmpeg systemctl; do
  command -v "$c" >/dev/null || die "'$c' não encontrado. Instale (ex.: sudo apt install git ffmpeg; uv: https://docs.astral.sh/uv/)"
done
UV="$(command -v uv)"
[ "$(id -u)" -ne 0 ] || die "rode como usuário normal, não como root"
USER_NAME="$(id -un)"

say "Dependências Python"
run uv sync --frozen

if [ -n "$bundle" ]; then
  say "Restaurando pacote $bundle"
  [ -f "$bundle" ] || die "pacote não encontrado: $bundle"
  if [ -e .env ] || [ -e data/canaisdark.db ]; then
    [ "$force" = 1 ] || die "já existe .env/banco aqui. Use --force para substituir (o atual vira .bak-<data>)"
    stamp="$(date +%Y%m%d-%H%M%S)"
    [ -e .env ] && run mv .env ".env.bak-$stamp"
    [ -d data ] && run mv data "data.bak-$stamp"
  fi
  run sudo systemctl stop canaisdark-worker canaisdark-web 2>/dev/null || true
  run tar -xzf "$bundle" -C .
  run chmod 600 .env
fi

[ -f .env ] || [ "$dry" = 1 ] || die ".env não existe. Copie o do outro PC (deploy/pack.sh) ou use --bundle."
if [ -f .env ]; then
  grep -q '^PUBLIC_URL=.\+' .env || echo "AVISO: PUBLIC_URL vazio no .env (ok só para testes locais)."
  grep -q '^SECRET_KEY=troque' .env && die "SECRET_KEY ainda é o valor padrão"
  grep -q '^FERNET_KEY=.\+' .env || echo "AVISO: FERNET_KEY vazio (necessário para YouTube/Drive)."
fi
run mkdir -p data/files

say "Backup do banco antes de migrar"
if [ -f data/canaisdark.db ]; then
  run mkdir -p data/backups
  run cp data/canaisdark.db "data/backups/pre-migrate-$(date +%Y%m%d-%H%M%S).db"
fi

say "Migrações"
run uv run --no-sync alembic upgrade head

say "Serviços systemd"
render() {
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@UV@|$UV|g" \
      -e "s|@PATH@|$(dirname "$UV"):/usr/local/bin:/usr/bin:/bin|g" "$1"
}
for name in web worker; do
  src="deploy/canaisdark-$name.service.in"
  dest="/etc/systemd/system/canaisdark-$name.service"
  if [ "$dry" = 1 ]; then echo "--- $dest"; render "$src"; else
    render "$src" | sudo tee "$dest" >/dev/null
  fi
done
run sudo systemctl daemon-reload
run sudo systemctl enable canaisdark-web canaisdark-worker
run sudo systemctl restart canaisdark-web canaisdark-worker

say "Versão instalada: $(uv run canaisdark version 2>/dev/null || echo desconhecida)"
say "Verificando"
if [ "$dry" = 0 ]; then
  for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null http://127.0.0.1:8123/login 2>/dev/null; then ok=1; break; fi
    sleep 1
  done
  [ "${ok:-0}" = 1 ] && echo "Site respondendo em http://127.0.0.1:8123" \
    || echo "AVISO: site não respondeu; veja: journalctl -u canaisdark-web -n 50"
  systemctl is-active canaisdark-web canaisdark-worker || true
fi

cat <<MSG

Pronto. Logs:  journalctl -u canaisdark-web -f   |   journalctl -u canaisdark-worker -f
Atualizar:     deploy/update.sh   (ou git pull && deploy/install.sh)
Falta (manual): subir o túnel do Cloudflare neste servidor com o MESMO token
(rota canaisdark.russi.app -> http://localhost:8123) e desligar o do PC antigo.
MSG
