from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.schemas.timeline import (
    MotionKind,
    SceneEdit,
    TextOverlay,
    Timeline,
    TransitionKind,
)
from canaisdark.services import timeline, videos
from canaisdark.services.errors import InvalidState
from tests.media import setup_video


@pytest.fixture
def ids(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return setup_video(session, user_token[0], [5.0, 4.0, 1.0, 6.0])


def test_schema_defaults_and_errors() -> None:
    tl = Timeline(scenes=[SceneEdit(scene_index=1), SceneEdit(scene_index=0)])
    assert [s.scene_index for s in tl.scenes] == [0, 1]  # ordenada
    assert tl.scenes[0].motion.kind == MotionKind.ZOOM_IN and tl.version == 1
    with pytest.raises(ValidationError, match="repetido"):
        Timeline(scenes=[SceneEdit(scene_index=0), SceneEdit(scene_index=0)])
    with pytest.raises(ValidationError):
        Timeline(scenes=[])
    with pytest.raises(ValidationError):
        SceneEdit(scene_index=0, motion={"kind": "spin"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="end_s"):
        TextOverlay(text="x", start_s=2, end_s=1)


def test_collect_timing_and_tail(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    t = timeline.collect_timing(session, video)
    assert [(round(s.start, 1), round(s.end, 1)) for s in t.spans] == [
        (0.0, 5.0),
        (5.0, 9.0),
        (9.0, 10.0),
        (10.0, 16.8),  # última cena: até o fim do áudio + cauda de 0,8 s
    ]
    assert t.duration == pytest.approx(16.8) and not t.partial


def test_default_timeline_motion_and_transitions(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    tl = timeline.build_default(session, video)
    kinds = [s.motion.kind for s in tl.scenes]
    assert all(a != b for a, b in zip(kinds, kinds[1:], strict=False))  # nunca repete em seguida
    assert tl.scenes[0].transition_in.kind == TransitionKind.CUT
    assert tl.scenes[1].transition_in.kind == TransitionKind.CROSSFADE
    assert tl.scenes[1].transition_in.duration == 0.6
    assert tl.scenes[2].transition_in.kind == TransitionKind.CUT  # cena de 1 s: corte
    assert tl.scenes[3].transition_in.kind == TransitionKind.CUT  # vizinha curta


def test_save_versions_and_hash(session: Session, ids: dict[str, int]) -> None:
    vid = ids["video_id"]
    video = videos.get_video(session, vid)
    a1 = timeline.save(session, vid, timeline.build_default(session, video), "auto", "sistema")
    a2 = timeline.save(session, vid, timeline.build_default(session, video), "user", "user:1")
    assert (a1.meta["number"], a2.meta["number"]) == (1, 2)
    asset, tl = timeline.latest(session, vid)  # type: ignore[misc]
    assert asset.id == a2.id and len(tl.scenes) == 4
    assert a1.meta["inputs_hash"] == timeline.inputs_hash(session, video)


def test_build_default_preserves_user_choices(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    base = timeline.build_default(session, video)
    base.scenes[1].texts.append(TextOverlay(text="1900"))
    again = timeline.build_default(session, video, base)
    assert again.scenes[1].texts[0].text == "1900"


def test_validation_messages(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    bad = Timeline(scenes=[SceneEdit(scene_index=9, asset_id=12345)])
    with pytest.raises(InvalidState, match="índices 0 a 3"):
        timeline.ensure_valid(session, video, bad)
    ok = Timeline(scenes=[SceneEdit(scene_index=0, asset_id=12345)])
    with pytest.raises(InvalidState, match="não é uma imagem/clipe"):
        timeline.ensure_valid(session, video, ok)


def test_requires_narration(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    from canaisdark.schemas.channel import ChannelCreate
    from canaisdark.schemas.script import SceneIn
    from canaisdark.schemas.video import VideoCreate
    from canaisdark.services import channels, scripts

    ch = channels.create_channel(session, ChannelCreate(name="C2"))
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user_token[0])  # type: ignore[arg-type]
    with pytest.raises(InvalidState, match="não foi aprovado"):
        timeline.collect_timing(session, v)
    scripts.save_user_script(session, v.id, [SceneIn(text="a")], user_token[0], approve=True)  # type: ignore[arg-type]
    with pytest.raises(InvalidState, match="narração ainda não"):
        timeline.collect_timing(session, v)
