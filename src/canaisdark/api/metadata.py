from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.services import metadata, thumbnail, videos

router = APIRouter(tags=["metadata"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/videos/{video_id}/metadata")
def get_metadata(_: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    video = videos.get_video(session, video_id)
    found = metadata.latest(session, video_id)
    thumb = thumbnail.latest(session, video_id)
    return {
        "current": metadata.current(video),
        "problems": metadata.problems(video),
        "proposal": (
            {
                "version": found[0].meta.get("number"),
                "applied": bool(found[0].meta.get("applied")),
                **found[1].model_dump(),
            }
            if found
            else None
        ),
        "thumbnail": {"asset_id": thumb.id, **thumb.meta} if thumb else None,
    }


@router.put("/videos/{video_id}/metadata", status_code=status.HTTP_201_CREATED)
def put_metadata(
    user: CurrentUser, session: SessionDep, video_id: int, body: MetadataIn, apply: bool = False
) -> dict[str, Any]:
    """Salva uma proposta (autor `user:<id>`); com `?apply=true` aplica ao vídeo na hora."""
    asset = metadata.save_proposal(session, video_id, body, "user", f"user:{user.id}")
    if apply:
        metadata.apply(session, video_id, user, asset.id)
    return {"version": asset.meta["number"], "applied": apply}


@router.post("/videos/{video_id}/metadata/apply")
def apply_metadata(user: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    video = metadata.apply(session, video_id, user)
    return metadata.current(video)


@router.put("/videos/{video_id}/thumbnail", status_code=status.HTTP_201_CREATED)
async def put_thumbnail(
    _: CurrentUser, session: SessionDep, video_id: int, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    asset = thumbnail.save_upload(session, video_id, await file.read())
    return {"asset_id": asset.id, **asset.meta}
