#!/usr/bin/env bash
# Empacota .env + banco + arquivos para levar a outra máquina.
# Rode na máquina de ORIGEM, na raiz do projeto:  deploy/pack.sh [saida.tar.gz]
# O pacote contém SEGREDOS (.env). Transfira por scp/rsync e apague depois.
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-canaisdark-bundle-$(date +%Y%m%d-%H%M%S).tar.gz}"
[ -f .env ] || { echo "ERRO: .env não encontrado em $(pwd)" >&2; exit 1; }
[ -f data/canaisdark.db ] || { echo "ERRO: data/canaisdark.db não encontrado" >&2; exit 1; }

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/data"
cp .env "$stage/.env"

# cópia consistente do banco (funciona com o servidor ligado; inclui o WAL)
python3 - "$stage/data/canaisdark.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/canaisdark.db")
dst = sqlite3.connect(sys.argv[1])
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
[ -d data/files ] && cp -a data/files "$stage/data/files"

umask 077
tar -czf "$out" -C "$stage" .env data
chmod 600 "$out"
echo "Pacote criado: $out ($(du -h "$out" | cut -f1))"
echo "Contém segredos. Envie com:  scp $out usuario@servidor:~/   e apague depois."
echo "Dica: pare o worker antes se houver job rodando, para não perder arquivos em andamento."
