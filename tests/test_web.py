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


def test_dashboard_attention_and_recent(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    from canaisdark.services import scripts, tasks

    logged.post("/channels", data={"name": "Canal Teste"})
    r = logged.post("/channels/canal-teste/videos", data={"topic": "Pronto para revisar"})
    vid = int(str(r.url).rsplit("/", 1)[1])
    home = logged.get("/").text
    assert (
        "Precisa da sua atenção" not in home and "Atividade recente" in home
    )  # tarefa aberta: ainda não é com você
    task = tasks.claim_next(session, user_token[0])
    assert task and task.id
    scripts.save_script(session, vid, [SceneIn(text="Um")], user_token[0], task_id=task.id)
    tasks.complete_task(session, task.id, user_token[0], "ok")
    home = logged.get("/").text
    assert "Precisa da sua atenção" in home and "Roteiro pronto para revisar" in home
    ver = scripts.get_script(session, vid)[0]
    assert ver.id
    scripts.approve_script(session, ver.id, user_token[0])
    assert "Precisa da sua atenção" not in logged.get("/").text

    r2 = logged.post("/channels/canal-teste/videos", data={"topic": "Vai falhar"})
    vid2 = int(str(r2.url).rsplit("/", 1)[1])
    t2 = tasks.claim_next(session, user_token[0], video_id=vid2)
    assert t2 and t2.id
    tasks.fail_task(session, t2.id, user_token[0], "sem contexto suficiente")
    assert "Tarefa de roteiro falhou: sem contexto suficiente" in logged.get("/").text


def test_tasks_page_has_process_all_prompt(logged: TestClient) -> None:
    html = logged.get("/tasks").text
    assert "Processar tudo de uma vez" in html and "claim_next_task" in html


def test_login_throttle(client: TestClient, user_token: tuple[User, str]) -> None:
    from canaisdark.services.ratelimit import login_limiter

    login_limiter.reset("testclient")
    for _ in range(10):
        assert client.post("/login", data={"token": "cd_errado"}).status_code == 401
    blocked = client.post("/login", data={"token": user_token[1]})
    assert blocked.status_code == 429 and "Muitas tentativas" in blocked.text
    login_limiter.reset("testclient")
    assert (
        client.post("/login", data={"token": user_token[1]}, follow_redirects=False).status_code
        == 303
    )


def test_public_url_requires_real_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from canaisdark.config import Settings, check_production_settings, public_host

    s = Settings(public_url="https://canaisdark.russi.app", secret_key="dev-insecure-change-me")
    with pytest.raises(RuntimeError):
        check_production_settings(s)
    ok = Settings(public_url="https://canaisdark.russi.app", secret_key="x" * 40)
    check_production_settings(ok)
    assert public_host(ok) == "canaisdark.russi.app"
    check_production_settings(Settings())  # dev local sem URL pública continua livre


def test_channel_tts_settings_roundtrip(logged: TestClient) -> None:
    logged.post(
        "/channels",
        data={
            "name": "Voz",
            "tts_model": "eleven_multilingual_v2",
            "tts_stability": "0.3",
            "tts_speed": "1.1",
        },
    )  # sem forced_alignment = desmarcado
    page = logged.get("/channels/voz?tab=settings").text
    assert (
        'value="eleven_multilingual_v2"' in page and 'value="0.3"' in page and 'value="1.1"' in page
    )
    assert "checked" not in page.split('name="forced_alignment"')[1].split(">")[0]
    logged.post("/channels/voz", data={"name": "Voz", "forced_alignment": "on"})
    assert (
        "checked"
        in logged.get("/channels/voz?tab=settings")
        .text.split('name="forced_alignment"')[1]
        .split(">")[0]
    )


def test_static_assets_are_cache_busted(logged: TestClient) -> None:
    html = logged.get("/").text
    import re

    assert re.search(r"/static/app\.css\?v=\d+", html) and re.search(
        r"/static/app\.js\?v=\d+", html
    )
    assert logged.get("/static/app.css?v=123").status_code == 200


def test_privacy_page_is_public(client: TestClient) -> None:
    r = client.get("/privacy", follow_redirects=False)
    assert r.status_code == 200
    assert "drive.file" in r.text
