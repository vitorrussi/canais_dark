#!/usr/bin/env bash
# Cria uma versão: confere tudo, atualiza pyproject, mescla dev em main (--no-ff) e cria a tag vX.Y.Z.
#   deploy/release.sh X.Y.Z [--dry-run] [--push] [--skip-tests]
# Rode em `dev`, com a árvore limpa e releases/vX.Y.Z.md escrito e revisado.
# Sem --push NADA é enviado: o script só mostra os comandos (regra do projeto: push só com ordem explícita).
set -euo pipefail
cd "$(dirname "$0")/.."

ver=""; dry=0; push=0; skip_tests=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --push) push=1; shift ;;
    --skip-tests) skip_tests=1; shift ;;
    -*) echo "uso: $0 X.Y.Z [--dry-run] [--push] [--skip-tests]" >&2; exit 2 ;;
    *) ver="$1"; shift ;;
  esac
done
ver="${ver#v}"
say() { printf '\n==> %s\n' "$*"; }
die() { echo "ERRO: $*" >&2; exit 1; }
run() { if [ "$dry" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "informe a versão no formato X.Y.Z (ex.: 0.9.0)"
tag="v$ver"

say "Conferindo o repositório"
[ "$(git branch --show-current)" = "dev" ] || die "rode na branch dev (atual: $(git branch --show-current))"
[ -z "$(git status --porcelain)" ] || die "há alterações não commitadas"
git rev-parse --verify -q main >/dev/null || die "branch main não existe"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "a tag $tag já existe"
last="$(git tag --list 'v*' --sort=-v:refname | head -1)"
if [ -n "$last" ]; then
  [ "$(printf '%s\n%s\n' "${last#v}" "$ver" | sort -V | tail -1)" = "$ver" ] && [ "${last#v}" != "$ver" ] \
    || die "a versão $ver deve ser maior que a última ($last)"
fi

say "Conferindo as notas de versão"
notes="releases/$tag.md"
[ -f "$notes" ] || die "falta $notes (escreva as notas em português simples; veja docs/releases.md)"
uv run python - "$notes" "$ver" <<'PY'
import sys
from pathlib import Path
from canaisdark.services import releases
r = releases.parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
if r.version != sys.argv[2]:
    sys.exit(f"o cabeçalho diz version: {r.version}, mas a release é {sys.argv[2]}")
if not r.sections:
    sys.exit("as notas não têm nenhuma seção com itens")
print(f"ok: {r.title} ({r.date}), seções: {', '.join(n for n, _ in r.sections)}")
PY

if [ "$skip_tests" = 0 ]; then
  say "ruff, mypy e testes"
  uv run ruff check src tests
  uv run mypy src
  uv run pytest -q
fi

say "Atualizando a versão para $ver"
run sed -i -E "0,/^version = \".*\"/s//version = \"$ver\"/" pyproject.toml
run uv lock
run git add pyproject.toml uv.lock
run git commit -m "chore(release): $tag"

say "Mesclando dev em main e criando a tag $tag"
run git checkout main
run git merge --no-ff dev -m "release: $tag"
run git tag -a "$tag" -m "Versão $ver"
run git checkout dev
run git merge --ff-only main

if [ "$push" = 1 ]; then
  say "Enviando"
  run git push origin main dev --follow-tags
else
  cat <<MSG

Pronto (nada foi enviado). Para publicar:
  git push origin main dev --follow-tags
No servidor:  deploy/update.sh
MSG
fi
