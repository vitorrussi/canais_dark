from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.job import JobRead
from canaisdark.services import channels, videos
from canaisdark.services import drive as drive_service

router = APIRouter(tags=["drive"])
SessionDep = Annotated[Session, Depends(get_session)]


class SyncRequest(BaseModel):
    # video, shorts, thumbnail, script, subtitles, audio, media (padrão: todas menos media)
    options: list[str] | None = None


@router.get("/videos/{video_id}/drive")
def get_drive(_: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    account = drive_service.get_account(session, channel.id or 0)
    return {
        "connected": bool(account and not account.needs_reconnect),
        "needs_reconnect": bool(account and account.needs_reconnect),
        "account_email": account.account_email if account else None,
        "folder_url": video.drive_url or None,
        "options": {
            "available": list(drive_service.ALL_OPTIONS),
            "default": list(drive_service.DEFAULT_OPTIONS),
        },
        "files": drive_service.overview(session, video),
    }


@router.post("/videos/{video_id}/drive/sync", status_code=status.HTTP_202_ACCEPTED)
def sync(user: CurrentUser, session: SessionDep, video_id: int, body: SyncRequest) -> JobRead:
    """Enfileira a sincronização com o Drive (só envia o que mudou). Só pela API/site, nunca por MCP."""
    job = drive_service.start_sync(session, video_id, user, body.options)
    return JobRead.model_validate(job, from_attributes=True)


@router.delete("/channels/{channel_id}/drive", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(_: CurrentUser, session: SessionDep, channel_id: int) -> None:
    drive_service.disconnect(session, channel_id)
