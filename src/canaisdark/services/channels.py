import re
import unicodedata

from sqlmodel import Session, select

from canaisdark.models import Channel
from canaisdark.models.channel import utcnow
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.services.errors import Conflict, NotFound


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def create_channel(session: Session, data: ChannelCreate) -> Channel:
    slug = slugify(data.name)
    if not slug:
        raise Conflict("O nome precisa ter ao menos uma letra ou número")
    if session.exec(select(Channel).where(Channel.slug == slug)).first():
        raise Conflict(f"Já existe um canal com o slug '{slug}'")
    channel = Channel(slug=slug, **data.model_dump())
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def get_channel(session: Session, channel_id: int) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFound(f"Canal {channel_id} não encontrado")
    return channel


def get_channel_by_slug(session: Session, slug: str) -> Channel:
    channel = session.exec(select(Channel).where(Channel.slug == slug)).first()
    if channel is None:
        raise NotFound(f"Canal '{slug}' não encontrado")
    return channel


def list_channels(session: Session, include_archived: bool = False) -> list[Channel]:
    stmt = select(Channel).order_by(Channel.name)
    if not include_archived:
        stmt = stmt.where(Channel.is_archived == False)  # noqa: E712
    return list(session.exec(stmt))


def update_channel(session: Session, channel_id: int, data: ChannelUpdate) -> Channel:
    channel = get_channel(session, channel_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(channel, key, value)
    channel.updated_at = utcnow()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def archive_channel(session: Session, channel_id: int) -> Channel:
    channel = get_channel(session, channel_id)
    channel.is_archived = True
    channel.updated_at = utcnow()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel
