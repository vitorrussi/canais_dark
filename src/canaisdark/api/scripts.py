from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.models import ScriptVersion
from canaisdark.schemas.script import (
    RevisionRequest,
    SaveScriptRequest,
    SceneRead,
    ScriptVersionRead,
)
from canaisdark.schemas.task import TaskRead
from canaisdark.services import scripts, videos

router = APIRouter(tags=["scripts"])
SessionDep = Annotated[Session, Depends(get_session)]


def _read(session: Session, version: ScriptVersion, with_scenes: bool) -> ScriptVersionRead:
    assert version.id is not None
    scenes = scripts.scenes_of(session, version)
    return ScriptVersionRead(
        id=version.id,
        video_id=version.video_id,
        number=version.number,
        approved=version.approved,
        approved_at=version.approved_at,
        author=version.author,
        created_at=version.created_at,
        scene_count=len(scenes),
        scenes=[SceneRead(index=s.index, text=s.text, notes=s.notes) for s in scenes]
        if with_scenes
        else None,
    )


@router.get("/videos/{video_id}/scripts")
def list_versions(_: CurrentUser, session: SessionDep, video_id: int) -> list[ScriptVersionRead]:
    videos.get_video(session, video_id)
    return [_read(session, v, False) for v in scripts.list_versions(session, video_id)]


@router.get("/scripts/{version_id}")
def get_version(_: CurrentUser, session: SessionDep, version_id: int) -> ScriptVersionRead:
    return _read(session, scripts.get_version(session, version_id), True)


@router.post("/scripts/{version_id}/approve")
def approve(user: CurrentUser, session: SessionDep, version_id: int) -> ScriptVersionRead:
    return _read(session, scripts.approve_script(session, version_id, user), False)


@router.post("/videos/{video_id}/script-revision", status_code=201)
def request_revision(
    user: CurrentUser, session: SessionDep, video_id: int, data: RevisionRequest
) -> TaskRead:
    task = scripts.request_revision(session, video_id, data.notes, user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.post("/videos/{video_id}/scripts", status_code=201)
def save_script(
    user: CurrentUser, session: SessionDep, video_id: int, data: SaveScriptRequest
) -> ScriptVersionRead:
    """Salva um roteiro escrito à mão como nova versão (autor `user`)."""
    version = scripts.save_user_script(session, video_id, data.scenes, user, data.approve)
    return _read(session, version, True)
