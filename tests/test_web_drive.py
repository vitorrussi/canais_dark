from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import drive as gd
from canaisdark.models import Asset, AssetType, User
from canaisdark.services import drive as drive_service
from canaisdark.services import timeline, videos
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
    monkeypatch.setattr(s, "drive_fake", True)
    monkeypatch.setattr(s, "fernet_key", Fernet.generate_key().decode())
    fake = gd.FakeDrive()
    monkeypatch.setattr(gd, "_demo", fake)
    ids = setup_video(session, user_token[0], [2.0, 2.0])
    video = videos.get_video(session, ids["video_id"])
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "files" / "final.mp4").write_bytes(b"F" * 1000)
    tl, _ = timeline.ensure_timeline(session, video.id or 0, "t")
    session.add(
        Asset(
            video_id=video.id or 0,
            type=AssetType.RENDER.value,
            path="final.mp4",
            meta={"kind": "final", "duration": 4, "bytes": 1000, "timeline_asset_id": tl.id},
        )
    )
    session.commit()
    client.post("/login", data={"token": user_token[1]})
    return {"c": client, "session": session, "user": user_token[0], "fake": fake, **ids}


def test_card_is_optional_collapsed_and_requires_connection(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    html = c.get(f"/videos/{vid}").text
    assert 'id="drive-card"' in html and "Google Drive" in html and "opcional" in html
    assert html.index("Metadados e YouTube") < html.index('id="drive-card"')
    assert "ainda não está conectado" in html
    assert "<details" in html and 'id="drive-card"' in html
    r = c.post(f"/videos/{vid}/drive/sync", data={"options": "video"}, follow_redirects=True)
    assert "não está conectado" in r.text


def test_sync_from_card_lists_files_and_links(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    session: Session = ctx["session"]  # type: ignore[assignment]
    vid = ctx["video_id"]
    c.get(f"/channels/{ctx['channel_id']}")  # sanity
    state = drive_service.make_state(int(ctx["channel_id"]), int(ctx["user"].id))  # type: ignore[call-overload,attr-defined]
    drive_service.complete_connection(session, "fake-code", state, ctx["user"])  # type: ignore[arg-type]
    page = c.get(f"/videos/{vid}").text
    assert "fake@example.com" in page and "novo" in page and "Sincronizar com o Drive" in page
    ok = c.post(
        f"/videos/{vid}/drive/sync",
        data={"options": ["video", "script"]},
        follow_redirects=True,
    )
    assert "Sincronização com o Drive enfileirada" in ok.text
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    done = c.get(f"/videos/{vid}").text
    assert "sincronizado" in done and "https://drive.fake/files/" in done
    assert "abrir a pasta do vídeo no Drive" in done
    assert "roteiro/roteiro.md" in done
    bad = c.post(f"/videos/{vid}/drive/sync", data={"options": ["xyz"]}, follow_redirects=True)
    assert "Opção desconhecida" in bad.text


def test_rest_drive(
    client: TestClient, ctx: dict[str, object], user_token: tuple[User, str]
) -> None:
    h = {"Authorization": f"Bearer {user_token[1]}"}
    vid = ctx["video_id"]
    assert client.get(f"/api/videos/{vid}/drive").status_code == 401
    got = client.get(f"/api/videos/{vid}/drive", headers=h).json()
    assert got["connected"] is False and "media" in got["options"]["available"]
    blocked = client.post(f"/api/videos/{vid}/drive/sync", headers=h, json={})
    assert blocked.status_code == 422 and "não está conectado" in blocked.text
    state = drive_service.make_state(int(ctx["channel_id"]), user_token[0].id or 0)  # type: ignore[call-overload]
    drive_service.complete_connection(ctx["session"], "fake-code", state, user_token[0])  # type: ignore[arg-type]
    ok = client.post(f"/api/videos/{vid}/drive/sync", headers=h, json={"options": ["video"]})
    assert ok.status_code == 202 and ok.json()["type"] == "drive_sync"
    assert client.delete(f"/api/channels/{ctx['channel_id']}/drive", headers=h).status_code == 204
