from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import TaskStatus, TaskType, User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, scripts, tasks, video_status, videos
from canaisdark.services.errors import Conflict, InvalidState, NotFound


@pytest.fixture
def ctx(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Session, User, int]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ch = channels.create_channel(session, ChannelCreate(name="C"))
    assert ch.id is not None
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user_token[0])
    assert v.id is not None
    return session, user_token[0], v.id


def _scenes(*texts: str) -> list[SceneIn]:
    return [SceneIn(text=t) for t in texts]


def test_save_creates_versions(ctx: tuple[Session, User, int]) -> None:
    s, user, vid = ctx
    v1 = scripts.save_script(s, vid, _scenes("Um", "Dois"), user)
    v2 = scripts.save_script(s, vid, _scenes("Novo"), user)
    assert (v1.number, v2.number) == (1, 2) and v1.author == f"claude:{user.id}"
    assert v1.full_text == "Um\n\nDois"
    version, scenes_ = scripts.get_script(s, vid)
    assert version.number == 2 and [x.text for x in scenes_] == ["Novo"]
    _, old = scripts.get_script(s, vid, number=1)
    assert [x.index for x in old] == [0, 1]
    assert [v.number for v in scripts.list_versions(s, vid)] == [2, 1]


def test_validation(ctx: tuple[Session, User, int]) -> None:
    s, user, vid = ctx
    with pytest.raises(InvalidState):
        scripts.save_script(s, vid, [], user)
    with pytest.raises(InvalidState):
        scripts.save_script(s, vid, _scenes("ok", "   "), user)
    with pytest.raises(NotFound):
        scripts.save_script(s, 999, _scenes("x"), user)
    with pytest.raises(NotFound):
        scripts.get_script(s, vid)


def test_approve_switches_approved_version(ctx: tuple[Session, User, int]) -> None:
    s, user, vid = ctx
    v1 = scripts.save_script(s, vid, _scenes("a"), user)
    v2 = scripts.save_script(s, vid, _scenes("b"), user)
    assert v1.id and v2.id
    scripts.approve_script(s, v1.id, user)
    scripts.approve_script(s, v2.id, user)
    s.refresh(v1)
    assert not v1.approved and v2.approved
    assert videos.get_video(s, vid).approved_script_version_id == v2.id


def test_revision_creates_task_with_notes(ctx: tuple[Session, User, int]) -> None:
    s, user, vid = ctx
    t = tasks.claim_next(s, user)
    assert t and t.id
    tasks.complete_task(s, t.id, user, "ok")
    scripts.save_script(s, vid, _scenes("a"), user)
    with pytest.raises(InvalidState):
        scripts.request_revision(s, vid, "  ", user)
    rev = scripts.request_revision(s, vid, "mais curto", user)
    assert rev.instructions == "mais curto" and rev.status == TaskStatus.PENDING.value
    with pytest.raises(Conflict):
        scripts.request_revision(s, vid, "outra", user)
    assert rev.type == TaskType.GENERATE_SCRIPT.value


def test_stage_states(ctx: tuple[Session, User, int]) -> None:
    s, user, vid = ctx

    def states() -> dict[str, str]:
        v = videos.get_video(s, vid)
        return {x.key: x.state for x in video_status.get_stages(s, v)}

    assert states()["script"] == "pending" and states()["script_approval"] == "not_started"
    t = tasks.claim_next(s, user)
    assert t and t.id and states()["script"] == "in_progress"
    version = scripts.save_script(s, vid, _scenes("a"), user, task_id=t.id)
    tasks.complete_task(s, t.id, user, "ok")
    assert states()["script"] == "done" and states()["script_approval"] == "pending"
    assert version.id
    scripts.approve_script(s, version.id, user)
    assert states()["script_approval"] == "done"


def test_delete_draft_with_script_and_tasks(ctx: tuple[Session, User, int]) -> None:
    s, user, vid = ctx
    scripts.save_script(s, vid, _scenes("a"), user)
    videos.delete_video(s, vid)
    assert tasks.list_tasks(s, video_id=vid) == []
