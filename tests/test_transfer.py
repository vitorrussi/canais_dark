from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, scripts, tasks, transfer, videos
from canaisdark.services.errors import Conflict, InvalidState


@pytest.fixture
def env(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Session, User, int, int]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    user = user_token[0]
    ch = channels.create_channel(
        session, ChannelCreate(name="Mistérios", visual_style_prompt="noir", extra_languages=["en"])
    )
    assert ch.id is not None
    v = videos.create_video(session, ch.id, VideoCreate(topic="Farol", title="O Farol"), user)
    assert v.id is not None
    scripts.save_script(session, v.id, [SceneIn(text="a"), SceneIn(text="b", notes="n")], user)
    v2 = scripts.save_script(session, v.id, [SceneIn(text="a2")], user)
    assert v2.id is not None
    scripts.approve_script(session, v2.id, user)
    return session, user, ch.id, v.id


def test_video_roundtrip(env: tuple[Session, User, int, int]) -> None:
    s, user, cid, vid = env
    raw = transfer.dumps(transfer.export_video(s, vid))
    bundle = transfer.parse_bundle(raw)
    assert bundle.format == "canaisdark.video" and len(bundle.scripts) == 2  # type: ignore[union-attr]
    new = transfer.import_video(s, cid, bundle, user, title="Cópia importada")  # type: ignore[arg-type]
    assert new.id != vid and new.title == "Cópia importada" and new.topic == "Farol"
    assert tasks.list_tasks(s, video_id=new.id) == []  # importar não aciona o Claude
    version, scenes = scripts.get_script(s, new.id or 0)
    assert version.number == 2 and version.approved and version.author.startswith("import:")
    assert [x.text for x in scenes] == ["a2"]
    _, first = scripts.get_script(s, new.id or 0, 1)
    assert first[1].notes == "n"
    assert new.approved_script_version_id == version.id


def test_channel_roundtrip_with_new_name(env: tuple[Session, User, int, int]) -> None:
    s, user, cid, _ = env
    bundle = transfer.parse_bundle(transfer.dumps(transfer.export_channel(s, cid)))
    same = transfer.import_channel(s, bundle, user)  # type: ignore[arg-type]
    assert same.name == "Mistérios (2)" and same.slug == "misterios-2"
    assert same.visual_style_prompt == "noir" and same.extra_languages == ["en"]
    assert len(videos.list_videos(s, same.id or 0)) == 1
    named = transfer.import_channel(s, bundle, user, name="Outro Nome", include_videos=False)  # type: ignore[arg-type]
    assert named.slug == "outro-nome" and videos.list_videos(s, named.id or 0) == []
    with pytest.raises(Conflict):
        transfer.import_channel(s, bundle, user, name="Mistérios")  # type: ignore[arg-type]


def test_parse_errors() -> None:
    for raw in (
        "não é json",
        "[]",
        '{"format": "outro"}',
        '{"format": "canaisdark.video", "version": 99, "exported_at": "x"}',
    ):
        with pytest.raises(InvalidState):
            transfer.parse_bundle(raw)
    with pytest.raises(InvalidState, match="video"):
        transfer.parse_bundle(
            '{"format": "canaisdark.video", "version": 1, "exported_at": "x", "video": {}}'
        )
