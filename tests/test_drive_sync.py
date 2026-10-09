from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import drive as gd
from canaisdark.models import Asset, AssetType, JobStatus, User
from canaisdark.services import drive as drive_service
from canaisdark.services import jobs, timeline, video_status, videos
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
    monkeypatch.setattr(s, "drive_fake", True)
    monkeypatch.setattr(s, "fernet_key", Fernet.generate_key().decode())
    fake = gd.FakeDrive()
    monkeypatch.setattr(gd, "_demo", fake)
    ids = setup_video(session, user_token[0], [2.0, 2.0])
    video = videos.get_video(session, ids["video_id"])
    video.title = "O Farol"
    session.add(video)
    session.commit()
    base = tmp_path / "files"
    final = base / "final.mp4"
    final.write_bytes(b"F" * 3000)
    tl, _ = timeline.ensure_timeline(session, video.id or 0, "t")
    session.add(
        Asset(
            video_id=video.id or 0,
            type=AssetType.RENDER.value,
            path="final.mp4",
            meta={"kind": "final", "duration": 4, "bytes": 3000, "timeline_asset_id": tl.id},
        )
    )
    session.commit()
    return {"fake": fake, "video": video, "user": user_token[0], "base": base, **ids}


def connect(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    state = drive_service.make_state(int(env["channel_id"]), user.id or 0)  # type: ignore[call-overload]
    drive_service.complete_connection(session, "fake-code", state, user)


def run_job(session: Session, job_id: int | None) -> JobStatus:
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    return JobStatus(jobs.get_job(session, job_id).status)  # type: ignore[arg-type]


def test_start_sync_blockers(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    with pytest.raises(InvalidState, match="não está conectado"):
        drive_service.start_sync(session, vid, user)
    connect(session, env)
    with pytest.raises(InvalidState, match="Opção desconhecida"):
        drive_service.start_sync(session, vid, user, ["tudo"])
    with pytest.raises(InvalidState, match="Ainda não há arquivos"):
        drive_service.start_sync(session, vid, user, ["thumbnail"])


def test_first_sync_then_only_changes(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: gd.FakeDrive = env["fake"]  # type: ignore[assignment]
    base: Path = env["base"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    connect(session, env)
    job = drive_service.start_sync(session, vid, user)
    assert run_job(session, job.id) == JobStatus.DONE
    names = sorted(n for _, n in fake.uploads)
    assert names == ["narracao.mp3", "o-farol.mp4", "roteiro.json", "roteiro.md"]
    video = videos.get_video(session, vid)
    assert video.drive_folder_id in fake.folders and video.drive_url.endswith(video.drive_folder_id)
    chain = []
    fid: str | None = video.drive_folder_id
    while fid:
        chain.append(str(fake.folders[fid]["name"]))
        fid = fake.folders[fid]["parent"]
    assert chain[0].startswith(f"#{vid} O Farol") and chain[-1] == "Estúdio de Canais Dark"
    assert len(chain) == 3
    rows = drive_service.overview(session, video)
    assert {r["state"] for r in rows} == {"synced"}
    assert video_status.stage_state(session, video, "drive") == "done"
    # 2ª vez: nada muda → nada enviado
    before = len(fake.uploads)
    job2 = drive_service.start_sync(session, vid, user)
    assert run_job(session, job2.id) == JobStatus.DONE
    assert len(fake.uploads) == before
    # muda só o vídeo final → só ele é atualizado, no mesmo arquivo
    file_id = next(
        a.meta["drive_file_id"]
        for a in drive_service.registry(session, vid).values()
        if a.meta["key"] == "render:final"
    )
    (base / "final.mp4").write_bytes(b"G" * 3500)
    assert video_status.stage_state(session, video, "drive") == "stale"
    job3 = drive_service.start_sync(session, vid, user)
    assert run_job(session, job3.id) == JobStatus.DONE
    assert fake.uploads[before:] == [("update", "o-farol.mp4")]
    assert fake.files[file_id]["bytes"] == b"G" * 3500
    assert len([f for f in fake.files.values() if f["name"] == "o-farol.mp4"]) == 1


def test_deleted_folder_and_file_are_recreated(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: gd.FakeDrive = env["fake"]  # type: ignore[assignment]
    base: Path = env["base"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    connect(session, env)
    assert run_job(session, drive_service.start_sync(session, vid, user).id) == JobStatus.DONE
    # a pasta do vídeo foi apagada no Drive
    old = videos.get_video(session, vid).drive_folder_id
    del fake.folders[old]
    fake.uploads.clear()
    assert run_job(session, drive_service.start_sync(session, vid, user).id) == JobStatus.DONE
    session.expire_all()
    new = videos.get_video(session, vid).drive_folder_id
    assert new != old and new in fake.folders
    assert sorted(a for a, _ in fake.uploads) == ["create"] * 4
    # só um arquivo foi apagado no Drive e mudou localmente → recria sem falhar
    reg = drive_service.registry(session, vid)
    gone = str(reg["render:final"].meta["drive_file_id"])
    del fake.files[gone]
    (base / "final.mp4").write_bytes(b"H" * 100)
    fake.uploads.clear()
    assert run_job(session, drive_service.start_sync(session, vid, user).id) == JobStatus.DONE
    assert fake.uploads == [("create", "o-farol.mp4")]


def test_partial_failure_then_retry_sends_only_the_missing(
    session: Session, env: dict[str, object]
) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: gd.FakeDrive = env["fake"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    connect(session, env)
    fake.fail_names["roteiro.md"] = gd.DriveError("sem permissão", "forbidden", 403)
    job = drive_service.start_sync(session, vid, user)
    assert run_job(session, job.id) == JobStatus.FAILED
    failed = jobs.get_job(session, job.id)  # type: ignore[arg-type]
    assert "roteiro.md" in failed.error and "com erro" in failed.error
    sent = {n for _, n in fake.uploads}
    assert "o-farol.mp4" in sent  # o que veio antes do erro ficou salvo
    video = videos.get_video(session, vid)
    assert video_status.stage_state(session, video, "drive") == "failed"
    fake.fail_names.clear()
    fake.uploads.clear()
    assert run_job(session, drive_service.start_sync(session, vid, user).id) == JobStatus.DONE
    assert ("create", "roteiro.md") in fake.uploads and (
        "create",
        "o-farol.mp4",
    ) not in fake.uploads


def test_storage_full_stops_and_reports(session: Session, env: dict[str, object]) -> None:
    user: User = env["user"]  # type: ignore[assignment]
    fake: gd.FakeDrive = env["fake"]  # type: ignore[assignment]
    vid = int(env["video_id"])  # type: ignore[call-overload]
    connect(session, env)
    full = gd.DriveError("O Google Drive está sem espaço.", "storage_full", 403)
    for n in ("narracao.mp3", "o-farol.mp4", "roteiro.json", "roteiro.md"):
        fake.fail_names[n] = full
    job = drive_service.start_sync(session, vid, user)
    assert run_job(session, job.id) == JobStatus.FAILED
    assert fake.uploads == []
    assert "sem espaço" in jobs.get_job(session, job.id).error  # type: ignore[arg-type]


def test_media_option_and_naming(session: Session, env: dict[str, object]) -> None:
    video = videos.get_video(session, int(env["video_id"]))  # type: ignore[call-overload]
    items = drive_service.build_manifest(session, video, ["media"])
    names = sorted(i.name for i in items)
    assert names == ["cena-001-main.jpg", "cena-002-main.jpg"]
    assert {i.folder for i in items} == {"midia"}
    assert drive_service._safe('a/b:c*"d') == "a-b-c--d"
