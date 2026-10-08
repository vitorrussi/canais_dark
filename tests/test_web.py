from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from canaisdark.config import get_settings
from canaisdark.models import User


@pytest.fixture
def logged(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    r = client.post("/login", data={"token": user_token[1]}, follow_redirects=False)
    assert r.status_code == 303
    return client


def test_redirects_when_anonymous(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/videos/1", follow_redirects=False).status_code == 303


def test_login_invalid_token(client: TestClient) -> None:
    assert client.post("/login", data={"token": "cd_x"}).status_code == 401


def test_full_flow(logged: TestClient) -> None:
    assert "Nenhum canal" in logged.get("/").text
    r = logged.post("/channels", data={"name": "Canal Teste", "language": "pt-BR"})
    assert (
        r.status_code == 200 and "Canal Teste" in r.text
    )  # seguiu redirect p/ /channels/canal-teste
    r = logged.post("/channels/canal-teste/videos", data={"topic": "Um tema"})
    assert r.status_code == 200 and "Roteiro" in r.text and "Não iniciada" in r.text
    assert "Um tema" in r.text
    r = logged.post(
        "/channels/canal-teste",
        data={"name": "Canal Teste", "language": "en", "visual_style_prompt": "noir"},
    )
    assert "noir" in r.text


def test_duplicate_channel_shows_error(logged: TestClient) -> None:
    logged.post("/channels", data={"name": "X"})
    r = logged.post("/channels", data={"name": "X"})
    assert r.status_code == 422 and "Já existe" in r.text


def test_logout(logged: TestClient) -> None:
    logged.post("/logout")
    assert logged.get("/", follow_redirects=False).status_code == 303


def test_static(client: TestClient) -> None:
    assert client.get("/static/htmx.min.js").status_code == 200
