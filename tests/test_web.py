from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.schemas.script import SceneIn


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
    assert r.status_code == 200 and "Roteiro" in r.text and "Pipeline" in r.text
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


def test_new_channel_page_has_presets_and_help(logged: TestClient) -> None:
    html = logged.get("/channels/new").text
    assert "Mistério" in html and "data-fill" in html
    assert "voice_id" in html and "Exemplo:" not in html  # o prefixo "Exemplo:" vem do CSS
    assert 'class="help"' in html


def test_channel_cards_show_stats(logged: TestClient) -> None:
    logged.post("/channels", data={"name": "Canal A"})
    logged.post("/channels/canal-a/videos", data={"topic": "x"})
    html = logged.get("/").text
    assert "1 vídeo" in html and "1 pendência" in html


def test_friendly_error_page(logged: TestClient) -> None:
    r = logged.get("/channels/inexistente")
    assert r.status_code == 404 and "não encontrado" in r.text


def _video_with_script(logged: TestClient, session: Session, user_token: tuple[User, str]) -> int:
    from canaisdark.schemas.script import SceneIn
    from canaisdark.services import scripts

    logged.post("/channels", data={"name": "Canal Teste"})
    r = logged.post("/channels/canal-teste/videos", data={"topic": "Tema"})
    vid = int(str(r.url).rsplit("/", 1)[1])
    scripts.save_script(
        session, vid, [SceneIn(text="Um velho texto"), SceneIn(text="Dois")], user_token[0]
    )
    return vid


def test_editor_page_embeds_data(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    vid = _video_with_script(logged, session, user_token)
    html = logged.get(f"/videos/{vid}/script/edit").text
    assert 'id="editor-data"' in html and "Um velho texto" in html and "script_editor.js" in html
    empty = logged.get(f"/videos/{vid + 1}/script/edit")
    assert empty.status_code == 404


def test_editor_save_creates_user_version(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    from canaisdark.services import scripts

    vid = _video_with_script(logged, session, user_token)
    r = logged.post(
        f"/videos/{vid}/script/save",
        json={
            "scenes": [{"text": "Um novo texto"}, {"text": "Dois"}, {"text": "Três"}],
            "approve": True,
        },
    )
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["url"].endswith("?v=2")
    version, scenes_ = scripts.get_script(session, vid)
    assert version.author.startswith("user:") and version.approved and len(scenes_) == 3
    assert (
        "Roteiro salvo como v2 e aprovado" in logged.get(f"/videos/{vid}").text
    )  # toast via flash
    assert logged.post(f"/videos/{vid}/script/save", json={"scenes": []}).status_code == 422


def test_diff_page(logged: TestClient, session: Session, user_token: tuple[User, str]) -> None:
    vid = _video_with_script(logged, session, user_token)
    logged.post(
        f"/videos/{vid}/script/save",
        json={"scenes": [{"text": "Um novo texto"}, {"text": "Dois"}, {"text": "Extra"}]},
    )
    html = logged.get(f"/videos/{vid}/diff?a=1&b=2").text
    assert "<del>velho</del>" in html and "<ins>novo</ins>" in html and "Extra" in html
    assert logged.get(f"/videos/{vid}/diff?a=1&b=9").status_code == 404


def test_channel_script_settings_flow_to_claude_context(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    from canaisdark.services import task_context, tasks

    logged.post(
        "/channels",
        data={
            "name": "Regras",
            "words_per_scene": "55",
            "script_rules": "Nunca citar vítimas",
            "default_script_instructions": "Fechar com pergunta",
        },
    )
    logged.post("/channels/regras/videos", data={"topic": "x"})
    task = tasks.claim_next(session, user_token[0])
    assert task
    ctx = task_context.build_task_context(session, task)["channel"]
    assert ctx["words_per_scene"] == 55 and ctx["script_rules"] == "Nunca citar vítimas"
    assert ctx["default_script_instructions"] == "Fechar com pergunta"
    page = logged.get("/channels/regras?tab=settings").text
    assert "Nunca citar vítimas" in page and 'value="55"' in page


def test_edit_duplicate_search_unarchive(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    from canaisdark.services import scripts, tasks, videos

    logged.post("/channels", data={"name": "Canal Teste"})
    r = logged.post(
        "/channels/canal-teste/videos",
        data={"topic": "Farol abandonado", "instructions": "tom investigativo"},
    )
    vid = int(str(r.url).rsplit("/", 1)[1])
    assert tasks.list_tasks(session, video_id=vid)[0].instructions == "tom investigativo"

    r = logged.post(
        f"/videos/{vid}/edit",
        data={
            "title": "O Farol",
            "topic": "Farol abandonado",
            "language": "en-US",
            "tags": "a, b",
            "description": "d",
        },
    )
    assert "Detalhes do vídeo salvos" in r.text
    video = videos.get_video(session, vid)
    assert (video.title, video.language, video.tags) == ("O Farol", "en-US", ["a", "b"])

    scripts.save_script(session, vid, [SceneIn(text="Um"), SceneIn(text="Dois")], user_token[0])
    r = logged.post(f"/videos/{vid}/duplicate")
    new_id = int(str(r.url).rsplit("/", 1)[1])
    assert new_id != vid and "O Farol (cópia)" in r.text and "Vídeo duplicado" in r.text
    assert len(scripts.get_script(session, new_id)[1]) == 2
    assert tasks.list_tasks(session, video_id=new_id) == []

    page = logged.get("/channels/canal-teste?q=cópia").text
    assert "O Farol (cópia)" in page and "Farol abandonado" not in page.split('id="video-list"')[
        1
    ].split("Novo vídeo")[0].replace("O Farol (cópia)", "")
    assert "Nada encontrado" in logged.get("/channels/canal-teste?q=zzz").text
    assert "Nada encontrado" in logged.get("/channels/canal-teste?status=published").text

    logged.post("/channels/canal-teste/archive")
    assert (
        "Canal Teste" not in logged.get("/").text and "arquivado" in logged.get("/?archived=1").text
    )
    assert "Canal reativado" in logged.post("/channels/canal-teste/unarchive").text
