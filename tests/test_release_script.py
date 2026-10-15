import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTE = """---
version: {v}
date: 2026-10-15
title: Teste
---
## Novidades
- Algo novo.
"""


def sh(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    work = tmp_path / "repo"
    (work / "deploy").mkdir(parents=True)
    for script in ("release.sh", "check_regressions.sh"):
        shutil.copy(ROOT / "deploy" / script, work / "deploy" / script)
    (work / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n')
    (work / "uv.lock").write_text("")
    (work / "releases").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "uv"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = run ] && [ "$2" = python ]; then shift 2; '
        f'PYTHONPATH={ROOT / "src"} exec {sys.executable} "$@"; fi\n'
        "exit 0\n"
    )
    shim.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for cmd in (
        ("git", "init", "-q", "-b", "main"),
        ("git", "add", "-A"),
        ("git", "commit", "-qm", "init"),
        ("git", "checkout", "-qb", "dev"),
    ):
        assert sh(work, *cmd, env=env).returncode == 0
    return work, env


def release(work: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return sh(work, "bash", "deploy/release.sh", *args, "--skip-tests", env=env)


def add_notes(work: Path, env: dict[str, str], version: str) -> None:
    (work / "releases" / f"v{version}.md").write_text(NOTE.format(v=version))
    sh(work, "git", "add", "-A", env=env)
    sh(work, "git", "commit", "-qm", "docs: notes", env=env)


def test_refuses_bad_version_branch_dirty_tree_and_missing_notes(
    repo: tuple[Path, dict[str, str]],
) -> None:
    work, env = repo
    assert "formato X.Y.Z" in release(work, env, "1.0").stderr
    assert "falta releases/v0.9.0.md" in release(work, env, "0.9.0").stderr
    add_notes(work, env, "0.9.0")
    (work / "sujo.txt").write_text("x")
    assert "alterações não commitadas" in release(work, env, "0.9.0").stderr
    (work / "sujo.txt").unlink()
    sh(work, "git", "checkout", "-q", "main", env=env)
    assert "rode na branch dev" in release(work, env, "0.9.0").stderr


def test_dry_run_changes_nothing_and_happy_path_tags_and_merges(
    repo: tuple[Path, dict[str, str]],
) -> None:
    work, env = repo
    add_notes(work, env, "0.9.0")
    dry = release(work, env, "0.9.0", "--dry-run")
    assert dry.returncode == 0 and "[dry-run]" in dry.stdout
    assert 'version = "0.1.0"' in (work / "pyproject.toml").read_text()
    assert sh(work, "git", "tag", env=env).stdout.strip() == ""

    done = release(work, env, "0.9.0")
    assert done.returncode == 0, done.stderr
    assert "git push origin main dev --follow-tags" in done.stdout  # não enviou
    assert sh(work, "git", "tag", env=env).stdout.split() == ["v0.9.0"]
    assert 'version = "0.9.0"' in (work / "pyproject.toml").read_text()
    assert sh(work, "git", "branch", "--show-current", env=env).stdout.strip() == "dev"
    main_sha = sh(work, "git", "rev-parse", "main", env=env).stdout
    assert main_sha == sh(work, "git", "rev-parse", "dev", env=env).stdout
    assert (
        sh(work, "git", "describe", "--tags", "--exact-match", "main", env=env).stdout.strip()
        == "v0.9.0"
    )


def test_next_version_must_be_greater(repo: tuple[Path, dict[str, str]]) -> None:
    work, env = repo
    add_notes(work, env, "0.9.0")
    assert release(work, env, "0.9.0").returncode == 0
    add_notes(work, env, "0.10.0")
    add_notes(work, env, "0.8.0")
    assert "deve ser maior" in release(work, env, "0.8.0").stderr
    assert release(work, env, "0.10.0").returncode == 0  # 0.10 > 0.9 (ordem numérica)
    assert sh(work, "git", "tag", env=env).stdout.split() == ["v0.10.0", "v0.9.0"]


def _commit(work: Path, env: dict[str, str], message: str, file: str) -> None:
    path = work / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(message)
    sh(work, "git", "add", "-A", env=env)
    assert sh(work, "git", "commit", "-qm", message, env=env).returncode == 0


def test_regression_check_requires_tests_for_fixes(repo: tuple[Path, dict[str, str]]) -> None:
    work, env = repo
    script = ["bash", "deploy/check_regressions.sh", "HEAD~3..HEAD"]
    _commit(work, env, "feat: algo novo", "src/a.py")
    _commit(work, env, "fix(ui): corrige botão", "src/b.py")
    _commit(work, env, "docs: texto", "docs/c.md")
    bad = sh(work, *script, env=env)
    assert (
        bad.returncode == 1
        and "SEM TESTE DE REGRESSÃO" in bad.stderr
        and "corrige botão" in bad.stderr
    )

    _commit(work, env, "fix: outro bug", "src/d.py")
    _commit(work, env, "test: cobre o botão", "tests/test_b.py")
    still = sh(work, "bash", "deploy/check_regressions.sh", "HEAD~2..HEAD", env=env)
    assert still.returncode == 1  # o teste veio em outro commit: cada fix traz o seu

    _commit(work, env, "fix(web): bug com teste junto", "src/e.py")
    (work / "tests").mkdir(exist_ok=True)
    (work / "tests" / "test_e.py").write_text("x")
    sh(work, "git", "add", "-A", env=env)
    sh(work, "git", "commit", "-q", "--amend", "--no-edit", env=env)
    ok = sh(work, "bash", "deploy/check_regressions.sh", "HEAD~1..HEAD", env=env)
    assert ok.returncode == 0 and "ok:" in ok.stdout

    _commit(work, env, "fix: typo no README [no-test: só documentação]", "README.md")
    skipped = sh(work, "bash", "deploy/check_regressions.sh", "HEAD~1..HEAD", env=env)
    assert skipped.returncode == 0
