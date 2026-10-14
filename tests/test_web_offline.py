from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services import scripts
from tests.media import setup_video


@pytest.fixture
def ctx(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ids = setup_video(session, user_token[0], [10.0, 10.0, 10.0])
    client.post("/login", data={"token": user_token[1]})
    return {"c": client, "session": session, "token": user_token[1], **ids}


def test_page_shows_prompt_and_applies_answer(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    page = c.get(f"/videos/{vid}/claude/generate_script")
    assert page.status_code == 200
    assert "Copiar pedido" in page.text and "Formato da resposta" in page.text
    r = c.post(
        f"/videos/{vid}/claude/generate_script",
        data={"answer": '```json\n{"scenes": [{"text": "Nova cena."}]}\n```'},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/videos/{vid}"
    _, scenes = scripts.get_script(ctx["session"], vid)  # type: ignore[arg-type]
    assert [s.text for s in scenes] == ["Nova cena."]


def test_invalid_answer_keeps_text_and_explains(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    r = c.post(f"/videos/{vid}/claude/generate_script", data={"answer": "não é json"})
    assert r.status_code == 422
    assert "Não consegui ler" in r.text and "não é json" in r.text


def test_unknown_type_is_rejected(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    r = c.get(f"/videos/{ctx['video_id']}/claude/nao_existe")
    assert r.status_code == 422


def test_rest_get_and_post(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    h = {"Authorization": f"Bearer {ctx['token']}"}
    got = c.get(f"/api/videos/{vid}/claude/generate_metadata", headers=h)
    assert got.status_code == 200 and "Formato da resposta" in got.json()["prompt"]
    ok = c.post(
        f"/api/videos/{vid}/claude/generate_metadata",
        headers=h,
        json={"answer": '{"title": "T", "description": "d", "tags": []}'},
    )
    assert ok.status_code == 200 and ok.json()["version"] == 1
    bad = c.post(f"/api/videos/{vid}/claude/generate_metadata", headers=h, json={"answer": "{}"})
    assert bad.status_code == 422


def test_cards_link_to_offline_mode(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    c.get(f"/videos/{vid}/claude/generate_metadata")  # cria a tarefa aberta de metadados
    page = c.get(f"/videos/{vid}/youtube/card")
    assert f"/videos/{vid}/claude/generate_metadata" in page.text


def test_copy_button_reads_a_hidden_textarea_with_the_full_prompt(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    page = c.get(f"/videos/{ctx['video_id']}/claude/generate_script").text
    assert 'data-copy="#offline-prompt-src"' in page
    area = page.split('id="offline-prompt-src"')[1].split("</textarea>")[0]
    assert "Formato da resposta" in area and "Canal Teste" in area
