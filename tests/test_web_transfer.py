import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services import scripts, tasks, videos


@pytest.fixture
def logged(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    client.post("/login", data={"token": user_token[1]})
    client.post("/channels", data={"name": "Canal Teste"})
    return client


def _video(logged: TestClient) -> int:
    r = logged.post("/channels/canal-teste/videos", data={"topic": "Tema"})
    return int(str(r.url).rsplit("/", 1)[1])


def test_download_formats(
    logged: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    vid = _video(logged)
    logged.post(
        f"/videos/{vid}/script/save",
        json={"scenes": [{"text": "Um"}, {"text": "Dois", "notes": "n"}]},
    )
    ver = scripts.get_script(session, vid)[0]
    txt = logged.get(f"/scripts/{ver.id}/download?format=txt")
    assert txt.status_code == 200 and txt.text == "Um\n\nDois\n"
    assert f"canal-teste-{vid}-v1.txt" in txt.headers["content-disposition"]
    assert "## Cena 2" in logged.get(f"/scripts/{ver.id}/download?format=md").text
    assert (
        json.loads(logged.get(f"/scripts/{ver.id}/download?format=json").text)["scenes"][1]["notes"]
        == "n"
    )
    assert logged.get(f"/scripts/{ver.id}/download?format=pdf").status_code == 422


def test_import_text_with_preview(logged: TestClient, session: Session) -> None:
    vid = _video(logged)
    text = "Primeira cena aqui.\n\nSegunda cena aqui."
    prev = logged.post(f"/videos/{vid}/script/import/preview", data={"paste": text, "rule": "auto"})
    assert "2 cenas" in prev.text and "Linha em branco" in prev.text
    bad = logged.post(f"/videos/{vid}/script/import/preview", data={"paste": "  ", "rule": "auto"})
    assert "vazio" in bad.text
    r = logged.post(
        f"/videos/{vid}/script/import", data={"paste": text, "rule": "blocks", "approve": "on"}
    )
    assert "Roteiro importado como v1" in r.text
    version, scenes_ = scripts.get_script(session, vid)
    assert version.approved and len(scenes_) == 2 and version.author.startswith("user:")


def test_import_from_uploaded_file(logged: TestClient, session: Session) -> None:
    vid = _video(logged)
    content = "# Título\n\n## Cena 1\n\nAbertura\n\n## Cena 2\n\nFim\n".encode()
    r = logged.post(
        f"/videos/{vid}/script/import",
        data={"rule": "auto"},
        files={"file": ("roteiro.md", content, "text/markdown")},
    )
    assert "v1" in r.text
    assert [s.text for s in scripts.get_script(session, vid)[1]] == ["Abertura", "Fim"]


def test_manual_video_without_claude(logged: TestClient, session: Session) -> None:
    r = logged.post(
        "/channels/canal-teste/videos/manual",
        data={"title": "Meu vídeo pronto", "paste": "Cena A\n\nCena B", "rule": "blocks"},
    )
    vid = int(str(r.url).rsplit("/", 1)[1])
    video = videos.get_video(session, vid)
    assert video.title == "Meu vídeo pronto" and video.topic == "Meu vídeo pronto"
    assert tasks.list_tasks(session, video_id=vid) == []
    assert len(scripts.get_script(session, vid)[1]) == 2


def test_manual_video_with_claude_review(logged: TestClient, session: Session) -> None:
    r = logged.post(
        "/channels/canal-teste/videos/manual",
        data={
            "title": "V2",
            "paste": "Só um bloco.",
            "ask_claude": "on",
            "claude_notes": "mais tenso",
        },
    )
    vid = int(str(r.url).rsplit("/", 1)[1])
    open_tasks = tasks.list_tasks(session, video_id=vid, open_only=True)
    assert len(open_tasks) == 1 and open_tasks[0].instructions == "mais tenso"


def test_manual_video_error_rerenders_form(logged: TestClient) -> None:
    r = logged.post("/channels/canal-teste/videos/manual", data={"title": "X", "paste": "  "})
    assert r.status_code == 422 and "vazio" in r.text and 'value="X"' in r.text


def test_export_and_import_bundles(logged: TestClient, session: Session) -> None:
    vid = _video(logged)
    logged.post(f"/videos/{vid}/script/save", json={"scenes": [{"text": "Um"}]})
    exported = logged.get(f"/videos/{vid}/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    chan = logged.get("/channels/canal-teste/export")
    assert (
        json.loads(chan.text)["format"] == "canaisdark.channel"
        and len(json.loads(chan.text)["videos"]) == 1
    )
    assert json.loads(logged.get("/channels/canal-teste/export?videos=0").text)["videos"] == []

    info = logged.post(
        "/import/inspect", files={"file": ("c.json", chan.content, "application/json")}
    )
    assert "Canal" in info.text and "1 vídeo" in info.text
    assert (
        "alert"
        in logged.post(
            "/import/inspect", files={"file": ("x.json", b"nao json", "application/json")}
        ).text
    )

    r = logged.post(
        "/import",
        data={"name": "Canal Importado", "include_videos": "on"},
        files={"file": ("c.json", chan.content, "application/json")},
    )
    assert "Canal Importado" in r.text and "importado" in r.text
    assert len(videos.list_videos(session, 2)) == 1

    r = logged.post(
        "/import",
        data={"name": "Vídeo copiado", "target_channel": "canal-importado"},
        files={"file": ("v.json", exported.content, "application/json")},
    )
    assert "Vídeo importado" in r.text and "Vídeo copiado" in r.text
    r = logged.post(
        "/import", data={}, files={"file": ("v.json", exported.content, "application/json")}
    )
    assert "Escolha o canal" in r.text


def test_import_pages_render(logged: TestClient) -> None:
    vid = _video(logged)
    assert "Importar roteiro" in logged.get(f"/videos/{vid}/script/import").text
    assert "Já tenho o roteiro" in logged.get("/channels/canal-teste").text
    assert "Importar canal ou vídeo" in logged.get("/import").text
    assert 'name="rule"' in logged.get("/channels/canal-teste/videos/manual").text
