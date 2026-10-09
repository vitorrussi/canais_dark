import threading
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark import db
from canaisdark.config import get_settings
from canaisdark.models import JobStatus, JobType, User
from canaisdark.models.channel import utcnow
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, jobs, videos
from canaisdark.services.errors import Conflict, InvalidState


@pytest.fixture
def vid(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ch = channels.create_channel(session, ChannelCreate(name="C"))
    assert ch.id
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user_token[0])
    assert v.id
    return v.id


def test_create_dedupe_and_flow(session: Session, vid: int) -> None:
    job = jobs.create_job(session, vid, JobType.TTS, {"n": 1})
    assert job.id and job.status == "queued" and job.params == {"n": 1}
    with pytest.raises(Conflict):
        jobs.create_job(session, vid, JobType.TTS)
    jobs.create_job(session, vid, JobType.ALIGNMENT)  # tipo diferente pode coexistir
    claimed = jobs.claim_next(session, "w1")
    assert (
        claimed and claimed.id == job.id and claimed.status == "running" and claimed.attempts == 1
    )
    jobs.append_log(session, job.id, "olá")
    jobs.heartbeat(session, job.id, 40)
    done = jobs.complete(session, job.id, {"ok": True})
    assert (
        done.status == "done"
        and done.progress == 100
        and "olá" in done.log
        and done.result == {"ok": True}
    )


def test_claim_empty_and_order(session: Session, vid: int) -> None:
    assert jobs.claim_next(session, "w") is None
    a = jobs.create_job(session, vid, JobType.TTS)
    b = jobs.create_job(session, vid, JobType.ALIGNMENT)
    assert jobs.claim_next(session, "w").id == a.id  # type: ignore[union-attr]
    assert jobs.claim_next(session, "w").id == b.id  # type: ignore[union-attr]
    assert jobs.claim_next(session, "w") is None


def test_concurrent_claim_single_winner(session: Session, vid: int) -> None:
    jobs.create_job(session, vid, JobType.TTS)
    results: list[object] = []
    barrier = threading.Barrier(2)

    def run(name: str) -> None:
        with Session(db.get_engine()) as s:
            barrier.wait()
            results.append(jobs.claim_next(s, name))

    ts = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r is not None for r in results) == [False, True]


def test_stale_recovery_cancel_retry(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    job = jobs.create_job(session, vid, JobType.TTS, {"x": 1})
    assert job.id
    jobs.claim_next(session, "w")
    job.heartbeat_at = utcnow() - timedelta(minutes=30)
    session.add(job)
    session.commit()
    assert [j.id for j in jobs.recover_stale(session)] == [job.id]
    session.refresh(job)
    assert job.status == "failed" and "worker" in job.error
    again = jobs.retry(session, job.id, user_token[0])
    assert again.parent_job_id == job.id and again.params == {"x": 1, "resume_from": job.id}
    with pytest.raises(InvalidState):
        jobs.retry(session, again.id or 0)  # ainda queued
    assert jobs.cancel(session, again.id or 0).status == JobStatus.CANCELLED.value
    assert jobs.is_cancelled(session, again.id or 0)
    with pytest.raises(InvalidState):
        jobs.cancel(session, again.id or 0)
    assert jobs.complete(session, again.id or 0).status == "cancelled"  # cancelado não "ressuscita"


def test_list_and_latest(session: Session, vid: int) -> None:
    a = jobs.create_job(session, vid, JobType.TTS)
    jobs.fail(session, a.id or 0, "erro")
    b = jobs.create_job(session, vid, JobType.TTS)
    assert jobs.latest_job(session, vid, JobType.TTS).id == b.id  # type: ignore[union-attr]
    assert [j.id for j in jobs.list_jobs(session, video_id=vid)] == [b.id, a.id]
    assert [j.id for j in jobs.list_jobs(session, status="failed")] == [a.id]
