import io
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import youtube as yt
from canaisdark.models import Asset, AssetType, TaskType, User
from canaisdark.services import tasks, timeline, videos
from canaisdark.services import youtube as yt_service
from canaisdark.worker.runner import Worker
from tests.media import setup_video


@pytest.fixture
def ctx(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "files")
    monkeypatch.setattr(s, "youtube_fake", True)
    monkeypatch.setattr(s, "fernet_key", Fernet.generate_key().decode())
    fake = yt.FakeYouTube()
    monkeypatch.setattr(yt, "_demo", fake)
    ids = setup_video(session, user_token[0], [2.0, 2.0])
    client.post("/login", data={"token": user_token[1]})
    return {"c": client, "session": session, "user": user_token[0], "fake": fake, **ids}


def _approved_final(ctx: dict[str, object]) -> None:
    session: Session = ctx["session"]  # type: ignore[assignment]
    user: User = ctx["user"]  # type: ignore[assignment]
    vid = int(ctx["video_id"])  # type: ignore[call-overload]
    video = videos.get_video(session, vid)
    tl, _ = timeline.ensure_timeline(session, vid, "t")
    out = get_settings().data_dir / "final.mp4"
    out.write_bytes(b"z" * 2000)
    session.add(
        Asset(
            video_id=vid,
            type=AssetType.RENDER.value,
            path="final.mp4",
            meta={
                "kind": "final",
                "duration": 4,
                "bytes": 2000,
                "timeline_asset_id": tl.id,
                "inputs_hash": timeline.inputs_hash(session, video),
                "approved_by": user.id,
                "approved_at": int(time.time()),
            },
        )
    )
    session.commit()


def test_card_flow_metadata_thumbnail_upload(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    session: Session = ctx["session"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    html = c.get(f"/videos/{vid}").text
    assert 'id="youtube-card"' in html and "Metadados e YouTube" in html
    assert html.index("Shorts 9:16") < html.index("Metadados e YouTube")
    assert "ainda não está conectado" in html
    c.get(f"/channels/{ctx['channel_id']}")  # sanity
    state = yt_service.make_state(int(ctx["channel_id"]), int(ctx["user"].id))  # type: ignore[call-overload,attr-defined]
    yt_service.complete_connection(session, "fake-code", state, ctx["user"])  # type: ignore[arg-type]
    c.post(
        f"/videos/{vid}/metadata/save",
        data={"title": "O farol", "description": "Texto", "tags": "farol, mistério\nnoite"},
    )
    page = c.get(f"/videos/{vid}").text
    assert 'value="O farol"' in page and "farol, mistério, noite" in page
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (1, 2, 3)).save(buf, "PNG")
    r = c.post(
        f"/videos/{vid}/thumbnail",
        files={"file": ("t.png", buf.getvalue(), "image/png")},
        follow_redirects=True,
    )
    assert "Thumbnail salva" in r.text
    blocked = c.post(
        f"/videos/{vid}/youtube/upload", data={"target": "long"}, follow_redirects=True
    )
    assert "render final" in blocked.text
    _approved_final(ctx)
    ok = c.post(f"/videos/{vid}/youtube/upload", data={"target": "long"}, follow_redirects=True)
    assert "enfileirado" in ok.text and "PRIVADO" in ok.text
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    done = c.get(f"/videos/{vid}").text
    assert "Vídeo enviado" in done and "https://youtu.be/fakeVid0001" in done
    assert "abrir no Studio" in done
    dup = c.post(f"/videos/{vid}/youtube/upload", data={"target": "long"}, follow_redirects=True)
    assert "já foi enviado" in dup.text


def test_invalid_metadata_and_claude_request(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    session: Session = ctx["session"]  # type: ignore[assignment]
    vid = int(ctx["video_id"])  # type: ignore[call-overload]
    bad = c.post(
        f"/videos/{vid}/metadata/save",
        data={"title": "a<b", "description": ""},
        follow_redirects=True,
    )
    assert "&lt; ou &gt;" in bad.text or "< ou >" in bad.text
    c.post(f"/videos/{vid}/metadata/request", data={"instructions": "mais curioso"})
    card = c.get(f"/videos/{vid}/youtube/card").text
    assert "Claude escrevendo os metadados" in card and "generate_metadata" in card
    types = {t.type for t in tasks.list_tasks(session, video_id=vid, open_only=True)}
    assert TaskType.GENERATE_METADATA.value in types
