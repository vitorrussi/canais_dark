from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.scene import SavePromptsRequest
from canaisdark.schemas.task import TaskCreate, TaskRead
from canaisdark.services import channels, scene_assets, scene_prompts, videos

router = APIRouter(tags=["scenes"])
SessionDep = Annotated[Session, Depends(get_session)]


def _asset(a: Any) -> dict[str, Any]:
    return {
        "id": a.id,
        "type": a.type,
        "variant": a.meta.get("variant", "main"),
        "width": a.meta.get("width"),
        "height": a.meta.get("height"),
        "duration": a.meta.get("duration"),
        "warnings": a.meta.get("warnings", []),
    }


@router.get("/videos/{video_id}/scenes")
def list_scenes(_: CurrentUser, session: SessionDep, video_id: int) -> list[dict[str, Any]]:
    """Cenas do roteiro aprovado com prompts (completos), flags e arquivos enviados."""
    channel = channels.get_channel(session, videos.get_video(session, video_id).channel_id)
    scenes = scene_prompts.approved_scenes(session, video_id)
    amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
    return [
        {
            "index": s.index,
            "text": s.text,
            "image_prompt": s.image_prompt,
            "image_prompt_full": scene_prompts.compose_prompt(channel, s),
            "video_prompt": s.video_prompt,
            "wants_vertical": s.wants_vertical,
            "is_key_scene": s.is_key_scene,
            "start_s": s.start_s,
            "end_s": s.end_s,
            "assets": {v: _asset(a) for v, a in amap.get(s.id or 0, {}).items()},
        }
        for s in scenes
    ]


@router.put("/videos/{video_id}/scene-prompts")
def save_prompts(
    _: CurrentUser, session: SessionDep, video_id: int, data: SavePromptsRequest
) -> dict[str, Any]:
    result = scene_prompts.save_prompts(session, video_id, data.prompts)
    return {"saved": result.saved, "missing": result.missing}


@router.post("/videos/{video_id}/prompts/request", status_code=status.HTTP_201_CREATED)
def request_prompts(
    user: CurrentUser, session: SessionDep, video_id: int, data: TaskCreate
) -> TaskRead:
    task = scene_prompts.request_prompts(session, video_id, data.instructions, user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.get("/videos/{video_id}/prompts")
def prompts_package(
    _: CurrentUser, session: SessionDep, video_id: int, format: str = "json"
) -> Response:
    name, mime, content = scene_prompts.package(session, video_id, format)
    return Response(
        content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.post("/videos/{video_id}/scenes/{scene_index}/upload", status_code=status.HTTP_201_CREATED)
async def upload(
    _: CurrentUser,
    session: SessionDep,
    video_id: int,
    scene_index: int,
    file: Annotated[UploadFile, File()],
    variant: Annotated[str, Form()] = "main",
) -> dict[str, Any]:
    """Um arquivo por requisição (limite `MAX_UPLOAD_MB`, padrão 100). `scene_index` é 0-based."""
    result = scene_assets.save_upload(
        session, video_id, scene_index, await file.read(), file.filename or "arquivo", variant
    )
    return {"replaced": result.replaced, "warnings": result.warnings, "asset": _asset(result.asset)}


@router.delete("/videos/{video_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(_: CurrentUser, session: SessionDep, video_id: int, asset_id: int) -> Response:
    scene_assets.remove_asset(session, video_id, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
