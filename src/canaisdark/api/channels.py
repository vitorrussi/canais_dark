from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.channel import ChannelCreate, ChannelRead, ChannelUpdate
from canaisdark.services import channels

router = APIRouter(prefix="/channels", tags=["channels"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("")
def list_channels(
    _: CurrentUser, session: SessionDep, include_archived: bool = False
) -> list[ChannelRead]:
    return [
        ChannelRead.model_validate(c, from_attributes=True)
        for c in channels.list_channels(session, include_archived)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_channel(_: CurrentUser, session: SessionDep, data: ChannelCreate) -> ChannelRead:
    return ChannelRead.model_validate(channels.create_channel(session, data), from_attributes=True)


@router.get("/{channel_id}")
def get_channel(_: CurrentUser, session: SessionDep, channel_id: int) -> ChannelRead:
    return ChannelRead.model_validate(
        channels.get_channel(session, channel_id), from_attributes=True
    )


@router.patch("/{channel_id}")
def update_channel(
    _: CurrentUser, session: SessionDep, channel_id: int, data: ChannelUpdate
) -> ChannelRead:
    return ChannelRead.model_validate(
        channels.update_channel(session, channel_id, data), from_attributes=True
    )


@router.post("/{channel_id}/archive")
def archive_channel(_: CurrentUser, session: SessionDep, channel_id: int) -> ChannelRead:
    return ChannelRead.model_validate(
        channels.archive_channel(session, channel_id), from_attributes=True
    )
