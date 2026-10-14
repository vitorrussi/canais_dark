from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.models import User
from canaisdark.services import releases
from canaisdark.web import releases as web_releases  # noqa: F401  (rota registrada)

NOTE = """---
version: {v}
date: 2026-10-15
title: {t}
---
## Correções
- Corrigido: o botão {v}.
## Novidades
- Agora dá   para fazer {v}.
Texto solto também vale.
## Vazia
"""


def _write(folder: Path, version: str, title: str = "Título") -> None:
    (folder / f"v{version}.md").write_text(NOTE.format(v=version, t=title), encoding="utf-8")


def test_parse_orders_sections_and_cleans_text() -> None:
    r = releases.parse(NOTE.format(v="0.9.0", t="Edição livre"))
    assert r.version == "0.9.0" and r.title == "Edição livre" and r.date == "2026-10-15"
    assert [name for name, _ in r.sections] == ["Novidades", "Correções"]  # ordem fixa; vazia some
    assert r.sections[0][1] == ["Agora dá para fazer 0.9.0.", "Texto solto também vale."]


@pytest.mark.parametrize(
    "text",
    [
        "sem cabeçalho",
        "---\nversion: 1.0\ndate: x\ntitle: t\n---\n- a",
        "---\nversion: 1.0.0\n---\n- a",
    ],
)
def test_parse_rejects_bad_notes(text: str) -> None:
    with pytest.raises(ValueError):
        releases.parse(text)


def test_load_all_sorts_semver_and_skips_broken(tmp_path: Path) -> None:
    for v in ("0.9.0", "0.10.0", "0.2.5"):
        _write(tmp_path, v)
    (tmp_path / "v9.9.9.md").write_text("lixo", encoding="utf-8")
    assert [r.version for r in releases.load_all(tmp_path)] == ["0.10.0", "0.9.0", "0.2.5"]
    assert releases.latest(tmp_path).version == "0.10.0"  # type: ignore[union-attr]
    assert releases.latest(tmp_path / "nao-existe") is None


@pytest.fixture
def logged(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    _write(tmp_path, "0.9.0", "Primeira")
    _write(tmp_path, "0.10.0", "Segunda")
    monkeypatch.setattr(releases, "RELEASES_DIR", tmp_path)
    client.post("/login", data={"token": user_token[1]})
    return client


def test_home_shows_new_badge_then_novidades_marks_seen(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    home = logged.get("/").text
    assert "Novidades na versão 0.10.0" in home and ">Novo<" in home
    page = logged.get("/novidades").text
    assert "Segunda" in page and "v0.10.0" in page and "Versões anteriores" in page
    assert "Primeira" in page and "Corrigido: o botão 0.10.0." in page
    session.expire_all()
    assert session.get(User, user_token[0].id).last_seen_version == "0.10.0"  # type: ignore[union-attr]
    again = logged.get("/").text
    assert "Novidades na versão" not in again and ">Novo<" not in again


def test_novidades_without_notes_is_friendly(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(releases, "RELEASES_DIR", tmp_path / "vazio")
    client.post("/login", data={"token": user_token[1]})
    page = client.get("/novidades").text
    assert "Ainda não há notas de versão" in page
    assert "v" in client.get("/").text  # rodapé com versão


def test_novidades_requires_login(client: TestClient) -> None:
    assert client.get("/novidades", follow_redirects=False).status_code == 303


def test_rich_formats_bold_and_code_and_escapes_html() -> None:
    out = releases.rich("**Novo:** use `make serve` <script>alert(1)</script>")
    assert "<strong>Novo:</strong>" in out and "<code>make serve</code>" in out
    assert "<script>" not in out and "&lt;script&gt;" in out
