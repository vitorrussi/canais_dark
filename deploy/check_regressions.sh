#!/usr/bin/env bash
# Exige teste de regressão em toda correção de bug: cada commit "fix..." do intervalo precisa alterar algo em tests/.
#   deploy/check_regressions.sh [intervalo]        (padrão: última tag..HEAD; sem tag, todo o histórico)
# Exceção explícita: "[no-test: motivo]" na mensagem do commit (ex.: correção só de documentação).
set -euo pipefail
cd "$(dirname "$0")/.."

range="${1:-}"
if [ -z "$range" ]; then
  last="$(git tag --list 'v*' --sort=-v:refname | head -1)"
  range="${last:+$last..}HEAD"
fi

bad=0
while read -r sha; do
  [ -n "$sha" ] || continue
  subject="$(git log -1 --pretty=%s "$sha")"
  body="$(git log -1 --pretty=%B "$sha")"
  [[ "$subject" =~ ^fix(\(.+\))?!?: ]] || continue
  if grep -q '\[no-test:' <<<"$body"; then continue; fi
  if git show --name-only --pretty= "$sha" | grep -q '^tests/'; then continue; fi
  echo "SEM TESTE DE REGRESSÃO: $(git log -1 --pretty='%h %s' "$sha")" >&2
  bad=1
done < <(git log --no-merges --pretty=%H "$range")

if [ "$bad" = 1 ]; then
  echo "Toda correção precisa de um teste que falhe sem ela (ou [no-test: motivo] na mensagem)." >&2
  exit 1
fi
echo "ok: todas as correções em $range têm teste de regressão"
