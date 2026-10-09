from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services import users


@pytest.fixture
def logged(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    client.post("/login", data={"token": user_token[1]})
    return client


def test_connect_page_shows_command_with_placeholder(logged: TestClient) -> None:
    html = logged.get(
        "/connect", headers={"host": "estudio.example.com", "x-forwarded-proto": "https"}
    ).text
    assert "claude mcp add --transport http canais https://estudio.example.com/mcp" in html
    assert "&lt;SEU_TOKEN&gt;" in html and "process_pending" in html


def test_new_token_rotates_and_shows_once(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    old = user_token[1]
    html = logged.post("/connect/token").text
    assert "não será mostrado de novo" in html
    new = html.split('id="token-box" class="promptbox">')[1].split("<")[0]
    assert new.startswith("cd_") and new != old
    assert users.authenticate_token(session, old) is None
    assert users.authenticate_token(session, new) is not None
    assert new not in logged.get("/connect").text  # só aparece uma vez
    assert logged.get("/", follow_redirects=False).status_code == 200  # sessão continua válida
