import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import youtube as yt
from canaisdark.models import Asset, AssetType, JobStatus, User
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.services import (
    jobs,
    metadata,
    render,
    shorts_render,
    thumbnail,
    video_status,
    videos,
)
from canaisdark.services import youtube as yt_service
from canaisdark.services.errors import InvalidState
from canaisdark.worker.runner import Worker
from tests.media import setup_video


@pytest.fixture
def env(
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
    video = videos.get_video(session, ids["video_id"])
    # canal conectado
    state = yt_service.make_state(ids["channel_id"], user_token[0].id or 0)
    yt_service.complete_connection(session, "fake-code", state, user_token[0])
    # render final aprovado (arquivo de mentira, mas existente)
    out = tmp_path / "files" / "final.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"x" * 5000)
    tl_asset, _ = __import__(
        "canaisdark.services.timeline", fromlist=["ensure_timeline"]
    ).ensure_timeline(session, video.id or 0, "t")
    from canaisdark.services import timeline

    inputs = timeline.inputs_hash(session, video)
    final = Asset(
        video_id=video.id or 0,
        type=AssetType.RENDER.value,
        path="final.mp4",
        meta={
            "kind": "final",
            "duration": 4.8,
            "bytes": 5000,
            "timeline_asset_id": tl_asset.id,
            "inputs_hash": inputs,
            "partial": False,
            "approved_by": user_token[0].id,
            "approved_at": int(time.time()),
        },
    )
    session.add(final)
    session.commit()
    return {"fake": fake, "video": video, "user": user_token[0], "final": final, **ids}


def test_upload_blockers_in_order(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    with pytest.raises(InvalidState, match="Falta o título"):
        yt_service.start_upload(session, vid, user)
    final: Asset = env["final"]  # type: ignore[assignment]
    final.meta = {**final.meta, "approved_by": None}
    session.add(final)
    session.commit()
    with pytest.raises(InvalidState, match="Aprove o vídeo"):
        yt_service.start_upload(session, vid, user)
    final.meta = {**final.meta, "partial": True}
    session.add(final)
    session.commit()
    with pytest.raises(InvalidState, match="teste curto"):
        yt_service.start_upload(session, vid, user)
    with pytest.raises(InvalidState, match="não tem render final"):
        yt_service.start_upload(session, vid, user, "short", 0)


def test_full_long_upload_with_thumbnail_and_duplicate_guard(
    session: Session, env: dict[str, object]
) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: yt.FakeYouTube = env["fake"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    metadata.apply(
        session,
        vid,
        user,
        edits=MetadataIn(title="O farol", description="Descrição", tags=["farol", "mistério"]),
    )
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (10, 20, 30)).save(buf, "PNG")
    thumbnail.save_upload(session, vid, buf.getvalue())
    job = yt_service.start_upload(session, vid, user)
    assert job.type == "youtube_upload"
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    done = jobs.get_job(session, job.id)  # type: ignore[arg-type]
    assert done.status == JobStatus.DONE.value, done.error + done.log
    up = fake.uploads[0]
    assert up["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    assert up["snippet"]["title"] == "O farol" and up["snippet"]["categoryId"] == "24"
    assert up["snippet"]["tags"] == ["farol", "mistério"]
    assert fake.thumbnails and fake.thumbnails[0][0] == up["id"]
    video = videos.get_video(session, vid)
    assert video.youtube_url == f"https://youtu.be/{up['id']}"
    stage = {s.key: s.state for s in video_status.get_stages(session, video)}
    assert stage["youtube"] == "done" and stage["metadata"] == "done"
    with pytest.raises(InvalidState, match="já foi enviado"):
        yt_service.start_upload(session, vid, user)
    again = yt_service.start_upload(session, vid, user, force=True)
    assert again.params["force"] is True
    q = yt_service.quota_info(session)
    assert q["used"] == yt_service.UPLOAD_COST + yt_service.THUMBNAIL_COST


def test_quota_guard_blocks_before_spending(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    metadata.apply(session, vid, user, edits=MetadataIn(title="T", description="D"))
    monkeypatch.setattr(get_settings(), "youtube_daily_quota", 1000)
    with pytest.raises(InvalidState, match="Cota do YouTube"):
        yt_service.start_upload(session, vid, user)


def test_quota_error_fails_job_clearly_and_invalid_grant_flags_account(
    session: Session, env: dict[str, object]
) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: yt.FakeYouTube = env["fake"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    metadata.apply(session, vid, user, edits=MetadataIn(title="T", description="D"))
    fake.fail_upload_with = yt.YouTubeError("A cota diária acabou.", "quota")
    job = yt_service.start_upload(session, vid, user)
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    failed = jobs.get_job(session, job.id)  # type: ignore[arg-type]
    assert failed.status == JobStatus.FAILED.value and "cota" in failed.error
    assert yt_service.list_uploads(session, vid) == []
    fake.fail_upload_with = yt.YouTubeError("revogado", "invalid_grant")
    j2 = yt_service.start_upload(session, vid, user)
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    assert jobs.get_job(session, j2.id).status == JobStatus.FAILED.value  # type: ignore[arg-type]
    with pytest.raises(InvalidState, match="expirou"):
        yt_service.start_upload(session, vid, user)


def test_short_upload_title_and_description(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: yt.FakeYouTube = env["fake"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    metadata.apply(session, vid, user, edits=MetadataIn(title="Longo", description="D"))
    out = get_settings().data_dir / "short1.mp4"
    out.write_bytes(b"y" * 3000)
    session.add(
        Asset(
            video_id=vid,
            type=AssetType.SHORT.value,
            path="short1.mp4",
            meta={"kind": "final", "index": 0, "title": "Quem acendia?", "duration": 30},
        )
    )
    video = videos.get_video(session, vid)
    video.youtube_url = "https://youtu.be/LONGO"
    session.add(video)
    session.commit()
    yt_service.start_upload(session, vid, user, "short", 0)
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    up = fake.uploads[0]
    assert up["snippet"]["title"] == "Quem acendia? #Shorts"
    assert "https://youtu.be/LONGO" in up["snippet"]["description"]
    assert up["status"]["privacyStatus"] == "private"
    assert videos.get_video(session, vid).youtube_url == "https://youtu.be/LONGO"  # não sobrescreve


def test_rest_youtube(
    client: TestClient, session: Session, env: dict[str, object], user_token: tuple[User, str]
) -> None:
    h = {"Authorization": f"Bearer {user_token[1]}"}
    vid = int(env["video_id"])  # type: ignore[call-overload]
    assert client.get(f"/api/videos/{vid}/youtube").status_code == 401
    got = client.get(f"/api/videos/{vid}/youtube", headers=h).json()
    assert got["connected"] is True and got["quota"]["limit"] == 10_000
    blocked = client.post(f"/api/videos/{vid}/youtube/upload", headers=h, json={})
    assert blocked.status_code == 422 and "título" in blocked.text
    client.put(
        f"/api/videos/{vid}/metadata?apply=true", headers=h, json={"title": "T", "description": "D"}
    )
    ok = client.post(f"/api/videos/{vid}/youtube/upload", headers=h, json={"target": "long"})
    assert ok.status_code == 202 and ok.json()["type"] == "youtube_upload"
    ch = int(env["channel_id"])  # type: ignore[call-overload]
    assert client.delete(f"/api/channels/{ch}/youtube", headers=h).status_code == 204
    assert render.latest_render(session, vid, "final") is not None
    assert shorts_render.list_shorts(session, vid) == []
