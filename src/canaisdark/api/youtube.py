from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.job import JobRead
from canaisdark.services import channels, shorts_render, videos
from canaisdark.services import youtube as yt_service

router = APIRouter(tags=["youtube"])
SessionDep = Annotated[Session, Depends(get_session)]


class UploadRequest(BaseModel):
    target: str = "long"  # long | short
    index: int | None = None  # Short (0-based)
    force: bool = False  # reenviar um arquivo que já foi enviado (cria duplicata)


@router.get("/videos/{video_id}/youtube")
def get_youtube(_: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    account = yt_service.get_account(session, channel.id or 0)
    return {
        "connected": bool(account and not account.needs_reconnect),
        "needs_reconnect": bool(account and account.needs_reconnect),
        "youtube_channel": account.youtube_channel_title if account else None,
        "quota": yt_service.quota_info(session),
        "uploads": [
            {"asset_id": u.id, **{k: v for k, v in u.meta.items() if k != "uploaded_by"}}
            for u in yt_service.list_uploads(session, video_id)
        ],
        "youtube_url": video.youtube_url,
        "shorts_final": sorted(
            int(a.meta.get("index", -1))
            for a in shorts_render.list_shorts(session, video_id, "final")
        ),
    }


@router.post("/videos/{video_id}/youtube/upload", status_code=status.HTTP_202_ACCEPTED)
def upload(user: CurrentUser, session: SessionDep, video_id: int, body: UploadRequest) -> JobRead:
    """Enfileira o envio PRIVADO ao YouTube. Só pela API/site, nunca por tool MCP."""
    job = yt_service.start_upload(session, video_id, user, body.target, body.index, body.force)
    return JobRead.model_validate(job, from_attributes=True)


@router.delete("/channels/{channel_id}/youtube", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(_: CurrentUser, session: SessionDep, channel_id: int) -> None:
    yt_service.disconnect(session, channel_id)
