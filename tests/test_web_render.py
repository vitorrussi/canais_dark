from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services import jobs, music, render, timeline, videos
from tests.media import have_ffmpeg, setup_video


@pytest.fixture
def ctx(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ids = setup_video(session, user_token[0], [2.0, 3.0, 2.5])
    client.post("/login", data={"token": user_token[1]})
    return {"c": client, "session": session, "user": user_token[0], **ids}


def test_video_page_has_numbered_steps_and_render_card(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    html = c.get(f"/videos/{ctx['video_id']}").text
    assert "Timeline e render" in html and 'id="render-card"' in html
    assert html.index("Roteiro") < html.index("Narração") < html.index("Timeline e render")
    assert "Prévia 720p" in html and "Render final 1080p" in html


def test_timeline_page_edit_and_versions(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    page = c.get(f"/videos/{vid}/timeline").text
    assert (
        "Timeline de edição" in page and 'name="motion_0"' in page and "padrão (não salva)" in page
    )
    r = c.post(
        f"/videos/{vid}/timeline",
        data={
            "motion_0": "pan_right",
            "intensity_0": "0.05",
            "trkind_0": "cut",
            "trdur_0": "0.6",
            "text_0": "Ilhas Flannan",
            "textpos_0": "top",
            "motion_1": "zoom_out",
            "intensity_1": "0.1",
            "trkind_1": "crossfade",
            "trdur_1": "0.4",
            "motion_2": "static",
            "intensity_2": "0",
            "trkind_2": "crossfade",
            "trdur_2": "0.6",
            "fade_in_s": "1.0",
            "fade_out_s": "1.0",
            "vignette": "on",
            "subtitles": "on",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200 and "Nova versão da timeline salva" in r.text
    _, tl = timeline.latest(ctx["session"], vid)  # type: ignore[arg-type,misc]
    assert tl.scenes[0].motion.kind == "pan_right" and tl.scenes[0].texts[0].text == "Ilhas Flannan"
    assert (
        tl.scenes[1].transition_in.duration == 0.4 and tl.subtitles.enabled and not tl.effects.grain
    )
    bad = c.post(
        f"/videos/{vid}/timeline/json", data={"payload": "{nao json"}, follow_redirects=True
    )
    assert "JSON inválido" in bad.text
    ok = c.post(
        f"/videos/{vid}/timeline/json",
        data={"payload": tl.model_dump_json()},
        follow_redirects=True,
    )
    assert "Nova versão" in ok.text and len(timeline.list_versions(ctx["session"], vid)) == 2  # type: ignore[arg-type]
    invalid = c.post(
        f"/videos/{vid}/timeline/json",
        data={"payload": '{"scenes":[{"scene_index":99}]}'},
        follow_redirects=True,
    )
    assert "índices 0 a 2" in invalid.text


def test_music_flow(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    bad = c.post(
        f"/videos/{vid}/music",
        files={"file": ("x.mp3", b"nao e audio", "audio/mpeg")},
        follow_redirects=True,
    )
    assert "não suportado" in bad.text
    ok = c.post(
        f"/videos/{vid}/music",
        files={"file": ("trilha.mp3", b"ID3" + b"\0" * 40, "audio/mpeg")},
        follow_redirects=True,
    )
    assert "Trilha enviada" in ok.text
    session: Session = ctx["session"]  # type: ignore[assignment]
    asset = music.list_music(session, vid)[0]  # type: ignore[arg-type]
    _, tl = timeline.latest(session, vid)  # type: ignore[arg-type,misc]
    assert tl.music.asset_id == asset.id
    c.post(
        f"/videos/{vid}/music/settings",
        data={"asset_id": str(asset.id), "volume_db": "-12", "ducking": "on"},
        follow_redirects=True,
    )
    _, tl = timeline.latest(session, vid)  # type: ignore[arg-type,misc]
    assert tl.music.volume_db == -12 and tl.music.ducking and not tl.subtitles.enabled
    c.post(f"/videos/{vid}/music/{asset.id}/remove", follow_redirects=True)
    _, tl = timeline.latest(session, vid)  # type: ignore[arg-type,misc]
    assert tl.music.asset_id is None and not music.list_music(session, vid)  # type: ignore[arg-type]


@pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg não instalado")
def test_render_start_card_and_approve_errors(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    r = c.post(f"/videos/{vid}/render/start", data={"quality": "preview"}, follow_redirects=True)
    assert "Render de prévia enfileirado" in r.text
    job = jobs.latest_job(ctx["session"], vid, jobs.JobType.RENDER)  # type: ignore[arg-type,attr-defined]
    assert job and job.status == "queued"
    card = c.get(f"/videos/{vid}/render/card").text
    assert 'hx-trigger="every 3s"' in card and "na fila" in card
    again = c.post(f"/videos/{vid}/render/start", data={"quality": "final"}, follow_redirects=True)
    assert "em andamento" in again.text
    nope = c.post(f"/videos/{vid}/render/approve", follow_redirects=True)
    assert "render final" in nope.text


def test_requires_login(client: TestClient) -> None:
    assert client.get("/videos/1/timeline", follow_redirects=False).status_code == 303
    assert client.post("/videos/1/render/start", follow_redirects=False).status_code == 303


def test_unused(session: Session) -> None:  # garante imports estáveis
    assert render.QUALITIES and videos
