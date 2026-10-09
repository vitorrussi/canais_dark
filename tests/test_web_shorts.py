from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services import shorts, video_status, videos
from tests.media import setup_video

TEXT = "Cena número 1. Cena número 2."


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
    return {"c": client, "session": session, **ids}


def test_optional_card_and_manual_add_remove(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    html = c.get(f"/videos/{vid}").text
    assert 'id="shorts-card"' in html and "opcional" in html
    assert html.index("Timeline e render") < html.index("Shorts 9:16")
    assert "Não vou fazer Shorts" in html
    r = c.post(f"/videos/{vid}/shorts/add", data={"title": "Abertura", "exact_text": TEXT})
    assert r.status_code in (200, 303)
    page = c.get(f"/videos/{vid}").text
    assert "1. Abertura" in page and "Prévia de todos" in page
    miss = c.post(
        f"/videos/{vid}/shorts/add",
        data={"title": "X", "exact_text": "isto nunca foi narrado em lugar nenhum"},
        follow_redirects=True,
    )
    assert "não foi encontrado" in miss.text
    c.post(f"/videos/{vid}/shorts/0/remove")
    assert "1. Abertura" not in c.get(f"/videos/{vid}").text


def test_skip_and_undo_do_not_block_anything(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    session: Session = ctx["session"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    c.post(f"/videos/{vid}/shorts/skip")
    page = c.get(f"/videos/{vid}").text
    assert "Reativar Shorts" in page and "dispensado" in page
    video = videos.get_video(session, int(vid))  # type: ignore[arg-type]
    states = {s.key: s.state for s in video_status.get_stages(session, video)}
    assert states["shorts"] == "skipped"
    assert shorts.latest_plan(session, int(vid)) is not None  # type: ignore[arg-type]
    c.post(f"/videos/{vid}/shorts/skip", data={"undo": "1"})
    assert "Reativar Shorts" not in c.get(f"/videos/{vid}").text


def test_request_claude_creates_task(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    c.post(f"/videos/{vid}/shorts/request", data={"instructions": "3 trechos"})
    page = c.get(f"/videos/{vid}/shorts/card").text
    assert "Claude escolhendo os trechos" in page and "select_shorts" in page
