from pathlib import Path

import pytest
from sqlmodel import Session, select

from canaisdark.config import get_settings
from canaisdark.models import User, Video, VideoStatus
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.video import VideoCreate, VideoUpdate
from canaisdark.services import channels, video_status, videos
from canaisdark.services.errors import InvalidState, NotFound


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return tmp_path / "files"


def _setup(session: Session, user_token: tuple[User, str]) -> tuple[int, User]:
    ch = channels.create_channel(session, ChannelCreate(name="Canal", language="en"))
    assert ch.id is not None
    return ch.id, user_token[0]


def test_create_inherits_language_and_makes_dir(
    session: Session, user_token: tuple[User, str], data_dir: Path
) -> None:
    cid, user = _setup(session, user_token)
    v = videos.create_video(session, cid, VideoCreate(topic="Tema"), user)
    assert v.language == "en" and v.status == VideoStatus.DRAFT
    assert (data_dir / "canal" / str(v.id)).is_dir()


def test_archived_channel_rejects_video(session: Session, user_token: tuple[User, str]) -> None:
    cid, user = _setup(session, user_token)
    channels.archive_channel(session, cid)
    with pytest.raises(InvalidState):
        videos.create_video(session, cid, VideoCreate(topic="x"), user)


def test_update_list_delete(session: Session, user_token: tuple[User, str], data_dir: Path) -> None:
    cid, user = _setup(session, user_token)
    v = videos.create_video(session, cid, VideoCreate(topic="Tema"), user)
    assert v.id is not None
    videos.update_video(session, v.id, VideoUpdate(title="T", tags=["a"]))
    assert videos.list_videos(session, cid)[0].title == "T"
    assert videos.list_videos(session, cid, status=VideoStatus.DRAFT)
    videos.delete_video(session, v.id)
    assert not (data_dir / "canal" / str(v.id)).exists()
    with pytest.raises(NotFound):
        videos.get_video(session, v.id)


def test_delete_only_draft(session: Session, user_token: tuple[User, str]) -> None:
    cid, user = _setup(session, user_token)
    v = videos.create_video(session, cid, VideoCreate(topic="x"), user)
    assert v.id is not None
    stored = session.get(Video, v.id)
    assert stored is not None
    stored.status = VideoStatus.IN_PRODUCTION
    session.add(stored)
    session.commit()
    with pytest.raises(InvalidState):
        videos.delete_video(session, v.id)


def test_stages_all_not_started(session: Session, user_token: tuple[User, str]) -> None:
    cid, user = _setup(session, user_token)
    v = videos.create_video(session, cid, VideoCreate(topic="x"), user)
    stages = video_status.get_stages(session, v)
    assert len(stages) == 13
    states = {s.key: s.state for s in stages}
    assert states["script"] == "pending"  # criar vídeo já enfileira a tarefa de roteiro
    assert {k for k, v in states.items() if v != "not_started"} == {"script"}


@pytest.mark.regression
def test_delete_draft_with_script_assets_and_jobs_does_not_violate_fks(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressão: apagar rascunho com roteiro aprovado, imagens e jobs falhava com FOREIGN KEY."""
    from canaisdark.models import Asset, Job, Scene
    from tests.media import setup_video

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ids = setup_video(session, user_token[0], [2.0, 3.0])
    vid = ids["video_id"]
    videos.delete_video(session, vid)
    session.expire_all()
    assert session.get(Video, vid) is None
    assert not session.exec(select(Asset).where(Asset.video_id == vid)).all()
    assert not session.exec(select(Scene).where(Scene.video_id == vid)).all()
    assert not session.exec(select(Job).where(Job.video_id == vid)).all()
