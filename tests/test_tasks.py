import threading
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark import db
from canaisdark.config import get_settings
from canaisdark.models import ClaudeTask, TaskStatus, TaskType, User
from canaisdark.models.channel import utcnow
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, tasks, users, videos
from canaisdark.services.errors import Conflict, InvalidState


@pytest.fixture
def video_id(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ch = channels.create_channel(session, ChannelCreate(name="C"))
    assert ch.id is not None
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user_token[0])
    assert v.id is not None
    return v.id


def _clear(session: Session, video_id: int) -> None:
    for t in tasks.list_tasks(session, video_id=video_id):
        session.delete(t)
    session.commit()


def test_dedupe(session: Session, video_id: int) -> None:
    _clear(session, video_id)
    tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)
    with pytest.raises(Conflict):
        tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)


def test_claim_complete_flow(session: Session, user_token: tuple[User, str], video_id: int) -> None:
    user = user_token[0]
    _clear(session, video_id)
    t = tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)
    assert t.id is not None
    claimed = tasks.claim_next(session, user)
    assert (
        claimed and claimed.id == t.id and claimed.status == "in_progress" and claimed.attempts == 1
    )
    assert tasks.claim_next(session, user) is None
    done = tasks.complete_task(session, t.id, user, "ok")
    assert done.status == TaskStatus.DONE.value and done.finished_at
    with pytest.raises(InvalidState):
        tasks.complete_task(session, t.id, user, "de novo")


def test_other_user_cannot_complete_until_expired(
    session: Session, user_token: tuple[User, str], video_id: int
) -> None:
    owner = user_token[0]
    other = users.create_user(session, "Outro", "outro@example.com")
    _clear(session, video_id)
    t = tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)
    assert t.id is not None
    tasks.claim_next(session, owner)
    with pytest.raises(Conflict):
        tasks.complete_task(session, t.id, other, "x")
    t.claimed_at = utcnow() - timedelta(minutes=31)
    session.add(t)
    session.commit()
    assert tasks.claim_next(session, other) is not None  # reserva expirada pode ser retomada
    assert tasks.get_task(session, t.id).claimed_by == other.id


def test_release_and_cancel(session: Session, user_token: tuple[User, str], video_id: int) -> None:
    user = user_token[0]
    _clear(session, video_id)
    t = tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)
    assert t.id is not None
    tasks.claim_next(session, user)
    assert tasks.release_task(session, t.id, user).status == "pending"
    assert tasks.cancel_task(session, t.id).status == "cancelled"
    with pytest.raises(InvalidState):
        tasks.cancel_task(session, t.id)


def test_claim_filters_by_type(
    session: Session, user_token: tuple[User, str], video_id: int
) -> None:
    _clear(session, video_id)
    tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)
    assert tasks.claim_next(session, user_token[0], TaskType.GENERATE_PROMPTS.value) is None


def test_concurrent_claim_gives_task_to_only_one(
    session: Session, user_token: tuple[User, str], video_id: int
) -> None:
    _clear(session, video_id)
    tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT)
    other = users.create_user(session, "Outro", "outro@example.com")
    results: list[ClaudeTask | None] = []
    barrier = threading.Barrier(2)

    def worker(actor_id: int) -> None:
        with Session(db.get_engine()) as s:
            actor = s.get(User, actor_id)
            assert actor is not None
            barrier.wait()
            results.append(tasks.claim_next(s, actor))

    assert other.id is not None and user_token[0].id is not None
    threads = [threading.Thread(target=worker, args=(i,)) for i in (user_token[0].id, other.id)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r is not None for r in results) == [False, True]


def test_claim_filters_by_video(
    session: Session, user_token: tuple[User, str], video_id: int
) -> None:
    assert tasks.claim_next(session, user_token[0], video_id=video_id + 1) is None
    assert tasks.claim_next(session, user_token[0], video_id=video_id) is not None
