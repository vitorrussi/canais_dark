# Versões, branches e notas de versão

## Branches
| Branch | Para quê |
|---|---|
| `main` | **sempre a última versão lançada** (é o que o servidor roda). Só recebe merges de release, cada um com tag `vX.Y.Z`. |
| `dev` | integração e testes: o dia a dia acontece aqui; o PC de desenvolvimento roda `dev`. |
| `feat/<nome>`, `fix/<nome>` | trabalho maior, opcional; sai de `dev` e volta para `dev`. |
| `hotfix/<nome>` | correção urgente: sai de `main`, volta para `main` (release de patch) e é mesclada em `dev`. |

Commits pequenos, Conventional Commits, testes verdes. **Push só quando a pessoa responsável mandar.**

## Versão
SemVer: `MAJOR.MINOR.PATCH`. Fonte única: `pyproject.toml`.
- **MINOR**: novidades que a pessoa vê. **PATCH**: correções. **MAJOR**: exige ação (ex.: migração manual).
- v0.9.0 = fases 0–8 + extensões; v1.0.0 = quando o deploy (Fase 9) estiver concluído.
- Em execução: a versão aparece na barra lateral e em `GET /health`; fora de uma tag aparece "em desenvolvimento".

## Notas de versão
Um arquivo por versão em `releases/vX.Y.Z.md`, em português simples (quem usa o site não é técnico):

```markdown
---
version: 0.9.0
date: 2026-10-15
title: Edição livre pelo Claude
---
## Novidades
- Agora o Claude pode editar o vídeo e criar a thumbnail por conta própria.
## Melhorias
- …
## Correções
- …
## Para quem administra
- (opcional) o que precisa ser feito ao atualizar.
```

Rascunho: `make release-notes` lista os commits desde a última tag, agrupados; o Claude traduz para linguagem simples
e a pessoa revisa. As notas aparecem em `/novidades`; quem ainda não viu a última versão vê um aviso "Novo".

## Lançar uma versão (no PC de desenvolvimento, em `dev`)
1. Escreva `releases/vX.Y.Z.md` (revisado).
2. `deploy/release.sh X.Y.Z --dry-run` e depois `deploy/release.sh X.Y.Z`: confere árvore limpa e testes, atualiza
   `pyproject.toml`, faz o commit de release, mescla `dev` em `main` (`--no-ff`) e cria a tag. Sem `--push`, ele só
   mostra os comandos de push.
3. No servidor: `deploy/update.sh` (busca tags, vai para a última, roda `install.sh`).
