import pytest
from sqlmodel import Session

from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.services import channels
from canaisdark.services.errors import Conflict, NotFound


def test_slugify() -> None:
    assert channels.slugify("Mistérios do Além!") == "misterios-do-alem"


def test_create_and_duplicate(session: Session) -> None:
    ch = channels.create_channel(session, ChannelCreate(name="Mistérios Dark"))
    assert ch.slug == "misterios-dark"
    with pytest.raises(Conflict):
        channels.create_channel(session, ChannelCreate(name="misterios dark"))


def test_invalid_name(session: Session) -> None:
    with pytest.raises(Conflict):
        channels.create_channel(session, ChannelCreate(name="!!!"))


def test_update_keeps_slug(session: Session) -> None:
    ch = channels.create_channel(session, ChannelCreate(name="A"))
    assert ch.id is not None
    upd = channels.update_channel(
        session, ch.id, ChannelUpdate(name="B", visual_style_prompt="noir", extra_languages=["en"])
    )
    assert (upd.slug, upd.name, upd.visual_style_prompt, upd.extra_languages) == (
        "a",
        "B",
        "noir",
        ["en"],
    )


def test_archive_hides_from_list(session: Session) -> None:
    ch = channels.create_channel(session, ChannelCreate(name="A"))
    assert ch.id is not None
    channels.archive_channel(session, ch.id)
    assert channels.list_channels(session) == []
    assert len(channels.list_channels(session, include_archived=True)) == 1


def test_not_found(session: Session) -> None:
    with pytest.raises(NotFound):
        channels.get_channel(session, 99)
