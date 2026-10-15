# Testes

## Como rodar
| Comando | O que faz |
|---|---|
| `make test` | suíte padrão (**sem** os testes que rodam o ffmpeg de verdade) |
| `make test-fast` | padrão sem os ponta a ponta |
| `make test-ffmpeg` | só os testes que usam o ffmpeg/ffprobe reais (opcional, local) |
| `make coverage` | cobertura por módulo (piso em `pyproject.toml`, `fail_under`) |
| `make check` | lint + mypy + testes + regressão obrigatória (o mesmo que o CI confere) |

Marcadores (`pyproject.toml`, `--strict-markers`): `ffmpeg` (aplicado sozinho a quem usa `needs_ffmpeg`),
`e2e` (ponta a ponta com provedores falsos), `regression` (protege uma correção). Nenhum teste usa rede.

## Guardas que se mantêm sozinhas
| Teste | O que garante |
|---|---|
| `test_routes_smoke.py` | toda rota HTTP (descoberta do app) exige login, exceto a lista pública explícita; nenhuma devolve 500 com dados reais |
| `test_mcp_contract.py` | toda tool MCP está em `docs/mcp-tools.md`, tem descrição, é chamada em algum teste e nenhuma envia/aprova |
| `test_docs_contract.py` | todo job tem handler, toda tarefa do Claude tem `HOW_TO`, migrações com uma só head |
| `test_e2e_pipeline.py` | roteiro → aprovação → narração → imagens → timeline → render (registrado) → metadados → thumbnail → YouTube (privado) → Drive → exportação |

Se você criar uma rota, tool ou job, essas guardas **falham** até a documentação/teste/handler existirem.

## Onde cada funcionalidade é testada
| Área | Arquivos |
|---|---|
| Canais, vídeos, roteiros, versões | `test_channels`, `test_videos`, `test_scripts`, `test_script_text`, `test_script_formats`, `test_transfer` |
| Tarefas do Claude, MCP, OAuth, copiar/colar | `test_tasks`, `test_mcp_*`, `test_oauth_flow`, `test_offline_tasks`, `test_web_offline`, `test_web_connect` |
| Narração, alinhamento, jobs, worker | `test_elevenlabs`, `test_narration_worker`, `test_alignment`, `test_jobs` |
| Storyboard, prompts, imagens | `test_scene_prompts`, `test_scene_assets`, `test_web_scenes` |
| Timeline, render, Shorts | `test_timeline`, `test_render`*, `test_web_render`, `test_shorts`, `test_web_shorts` |
| YouTube, Drive | `test_youtube_*`, `test_web_youtube`, `test_drive_*`, `test_web_drive` |
| Edição livre e thumbnail pelo Claude | `test_edit_package` |
| Versões e novidades | `test_version`, `test_releases`, `test_release_script` |
| Usuários, admin, migrações, CLI | `test_users`, `test_admin`, `test_migrations`, `test_cli`, `test_crypto` |

\* parte dos testes de render roda o ffmpeg real e fica no marcador `ffmpeg`.

## Regra: todo bug corrigido ganha teste de regressão
1. Reproduza o bug em um teste que **falhe sem a correção** (confira com `git stash push <arquivo-do-código>`).
2. Marque com `@pytest.mark.regression` e descreva o sintoma na docstring ("Regressão: …").
3. Commit `fix(...)` **com o teste junto**. `deploy/check_regressions.sh` (CI, `make check` e `release.sh`) recusa
   commits `fix` sem alteração em `tests/`; exceção documentada: `[no-test: motivo]` na mensagem.
4. Achou o bug por uma guarda (smoke etc.)? Mesmo assim, o teste de regressão específico entra junto da correção.

Histórico anterior à regra: os `fix` de `and_`/`or_` (tarefas e janela de desfazer) e das migrações em lote do
SQLite são cobertos por `test_tasks`, `test_narration_worker` e `test_migrations`.
