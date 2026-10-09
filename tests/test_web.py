from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

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
    assert "Copiar prompt" in r.text and "claim_next_task(video_id=" in r.text
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


def test_script_review_flow(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    from canaisdark.schemas.script import SceneIn
    from canaisdark.services import scripts, tasks

    user = user_token[0]
    logged.post("/channels", data={"name": "Canal Teste"})
    r = logged.post("/channels/canal-teste/videos", data={"topic": "Um tema"})
    assert "aguardando o Claude" in r.text
    vid = int(str(r.url).rsplit("/", 1)[1])
    assert "Pendências do Claude" in logged.get("/tasks").text

    task = tasks.claim_next(session, user)
    assert task and task.id
    ver = scripts.save_script(
        session, vid, [SceneIn(text="Primeira cena"), SceneIn(text="Segunda")], user
    )
    tasks.complete_task(session, task.id, user, "ok")

    page = logged.get(f"/videos/{vid}").text
    assert "Primeira cena" in page and "Aprovar roteiro" in page

    page = logged.post(f"/scripts/{ver.id}/approve").text
    assert "aprovada" in page and "Aprovar roteiro" not in page

    page = logged.post(f"/videos/{vid}/script/revision", data={"notes": "mais curto"}).text
    assert "mais curto" in page and "aguardando o Claude" in page
    page = logged.post(f"/videos/{vid}/script/revision", data={"notes": "outra"}).text
    assert "Já há uma tarefa" in page  # flash de erro
    tid = tasks.list_tasks(session, open_only=True)[0].id
    page = logged.post(f"/tasks/{tid}/cancel").text
    assert "aguardando o Claude" not in page
