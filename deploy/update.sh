#!/usr/bin/env bash
# Atualiza o servidor para a última versão lançada (branch main) e reinstala.
#   deploy/update.sh [--tag vX.Y.Z]   # --tag: volta/avança para uma versão específica (fica "detached")
set -euo pipefail
cd "$(dirname "$0")/.."

tag=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) tag="${2:?informe a tag}"; shift 2 ;;
    *) echo "uso: $0 [--tag vX.Y.Z]" >&2; exit 2 ;;
  esac
done
die() { echo "ERRO: $*" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || die "há alterações locais não commitadas; resolva antes de atualizar"

git fetch --tags --prune origin
if [ -n "$tag" ]; then
  git rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag não existe"
  git checkout -q "$tag"
else
  git checkout -q main
  git merge --ff-only origin/main
fi
echo "Versão: $(uv run canaisdark version 2>/dev/null || git describe --tags --always)"
exec deploy/install.sh
