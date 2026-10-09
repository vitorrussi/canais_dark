"""Storyboard: prompts por cena, uploads de imagens/clipes (1 arquivo por requisição) e pacote de prompts."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import Asset, Scene
from canaisdark.schemas.scene import SceneUpdate
from canaisdark.services import (
    channels,
    scene_assets,
    scene_prompts,
    task_context,
    tasks,
    videos,
)
from canaisdark.services.errors import DomainError, NotFound
from canaisdark.web.common import SessionDep, WebUser, back, render, templates

router = APIRouter(include_in_schema=False)


def _asset_json(a: Asset) -> dict[str, Any]:
    return {
        "id": a.id,
        "type": a.type,
        "variant": a.meta.get("variant", "main"),
        "file_url": f"/assets/{a.id}/file",
        "thumb_url": f"/assets/{a.id}/thumb" if a.meta.get("thumb") else None,
        "width": a.meta.get("width"),
        "height": a.meta.get("height"),
        "duration": a.meta.get("duration"),
        "warnings": a.meta.get("warnings", []),
        "name": a.meta.get("original_name", ""),
    }


def _scene_rows(session: Session, video_id: int, scenes: list[Scene]) -> list[dict[str, Any]]:
    channel = channels.get_channel(session, videos.get_video(session, video_id).channel_id)
    amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
    rows = []
    for s in scenes:
        present = amap.get(s.id or 0, {})
        rows.append(
            {
                "scene": s,
                "full": scene_prompts.compose_prompt(channel, s),
                "full_vertical": scene_prompts.compose_prompt(channel, s, True),
                "assets": {v: _asset_json(a) for v, a in present.items()},
                "complete": "main" in present and (not s.wants_vertical or "vertical" in present),
            }
        )
    return rows


@router.get("/videos/{video_id}/scenes", response_class=HTMLResponse)
def scenes_page(
    request: Request, user: WebUser, session: SessionDep, video_id: int, only: str = ""
) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    crumbs = [
        ("Canais", "/"),
        (channel.name, f"/channels/{channel.slug}"),
        (f"Vídeo #{video.id}", f"/videos/{video.id}"),
    ]
    try:
        scenes = scene_prompts.approved_scenes(session, video_id)
    except DomainError as e:
        return render(
            request,
            "scenes.html",
            user,
            video=video,
            channel=channel,
            rows=[],
            blocked=str(e),
            crumbs=crumbs,
        )
    rows = _scene_rows(session, video_id, scenes)
    have_prompts, total = scene_prompts.prompt_coverage(scenes)
    have_files, needed_files = scene_assets.coverage(session, scenes)
    if only == "pending":
        rows = [r for r in rows if not r["complete"] or not r["scene"].image_prompt.strip()]
    open_task = next(
        (
            t
            for t in tasks.list_tasks(session, video_id=video_id, open_only=True)
            if t.type == "generate_prompts"
        ),
        None,
    )
    claude_prompt = (
        task_context.build_user_prompt(video_id, video.topic, channel.name, "generate_prompts")
        if open_task
        else None
    )
    return render(
        request,
        "scenes.html",
        user,
        video=video,
        channel=channel,
        rows=rows,
        blocked="",
        total=total,
        have_prompts=have_prompts,
        have_files=have_files,
        needed_files=needed_files,
        open_task=open_task,
        claude_prompt=claude_prompt,
        only=only,
        max_mb=get_settings().max_upload_mb,
        crumbs=crumbs,
    )


@router.get("/videos/{video_id}/scenes/batches", response_class=HTMLResponse)
def prompt_batches(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    size: int = 10,
    variant: str = "main",
    only_missing: int = 0,
    per: int = 0,
) -> Response:
    """Parcial HTMX: prompts em lote (uma mensagem por lote) para colar no Gemini/ChatGPT."""
    size = max(0, min(size, 200))
    data = scene_prompts.batch_prompts(
        session, video_id, size, variant == "vertical", bool(only_missing), max(0, min(per, 50))
    )
    return templates.TemplateResponse(
        request,
        "_prompt_batches.html",
        {
            "user": user,
            "video_id": video_id,
            "size": size,
            "per": per,
            "variant": variant,
            "only_missing": only_missing,
            **data,
        },
    )


@router.post("/videos/{video_id}/scenes/vertical")
def set_all_vertical(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    enabled: Annotated[int, Form()] = 0,
) -> Response:
    try:
        n = scene_prompts.set_all_vertical(session, video_id, bool(enabled))
    except DomainError as e:
        return back(request, f"/videos/{video_id}/scenes", error=e)
    msg = f"9:16 marcado em {n} cenas" if enabled else f"9:16 desmarcado em {n} cenas"
    return back(request, f"/videos/{video_id}/scenes", ok=msg)


@router.post("/videos/{video_id}/prompts/request")
def request_prompts(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    instructions: Annotated[str, Form()] = "",
) -> Response:
    try:
        scene_prompts.request_prompts(session, video_id, instructions, user)
    except DomainError as e:
        return back(request, f"/videos/{video_id}/scenes", error=e)
    return back(
        request, f"/videos/{video_id}/scenes", ok="Tarefa de prompts enfileirada para o Claude"
    )


@router.post("/videos/{video_id}/scenes/{scene_id}/update")
def update_scene(
    user: WebUser, session: SessionDep, video_id: int, scene_id: int, data: SceneUpdate
) -> Response:
    """Autosave do prompt/flags (JSON). Devolve os prompts completos para atualizar os botões de copiar."""
    try:
        scene = scene_prompts.update_scene(session, video_id, scene_id, data)
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    channel = channels.get_channel(session, videos.get_video(session, video_id).channel_id)
    return JSONResponse(
        {
            "ok": True,
            "full": scene_prompts.compose_prompt(channel, scene),
            "full_vertical": scene_prompts.compose_prompt(channel, scene, True),
            "wants_vertical": scene.wants_vertical,
            "is_key_scene": scene.is_key_scene,
        }
    )


@router.post("/videos/{video_id}/scenes/{scene_index}/upload")
async def upload(
    user: WebUser,
    session: SessionDep,
    video_id: int,
    scene_index: int,
    file: Annotated[UploadFile, File()],
    variant: Annotated[str, Form()] = "main",
) -> Response:
    """Um arquivo por requisição (o Cloudflare limita o corpo a 100 MB)."""
    data = await file.read()
    try:
        result = scene_assets.save_upload(
            session, video_id, scene_index, data, file.filename or "arquivo", variant
        )
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return JSONResponse(
        {"ok": True, "replaced": result.replaced, "asset": _asset_json(result.asset)}
    )


class BatchPlanRequest(BaseModel):
    names: list[str]
    variant: str = "main"
    mtimes: list[float] | None = None  # File.lastModified (ms) de cada arquivo


@router.post("/videos/{video_id}/assets/batch-plan")
def batch_plan(
    user: WebUser, session: SessionDep, video_id: int, data: BatchPlanRequest
) -> Response:
    try:
        scenes = scene_prompts.approved_scenes(session, video_id)
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
    filled = {s.index for s in scenes if data.variant in amap.get(s.id or 0, {})}
    return JSONResponse(
        {
            "plan": scene_assets.map_filenames(data.names, len(scenes), filled, data.mtimes),
            "scene_count": len(scenes),
        }
    )


@router.post("/videos/{video_id}/assets/{asset_id}/delete")
def delete_asset(user: WebUser, session: SessionDep, video_id: int, asset_id: int) -> Response:
    try:
        scene_assets.remove_asset(session, video_id, asset_id)
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return JSONResponse({"ok": True})


@router.get("/videos/{video_id}/prompts.{fmt}")
def download_prompts(user: WebUser, session: SessionDep, video_id: int, fmt: str) -> Response:
    name, mime, content = scene_prompts.package(session, video_id, fmt)
    return Response(
        content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/assets/{asset_id}/thumb")
def thumb(user: WebUser, session: SessionDep, asset_id: int) -> Response:
    asset = session.get(Asset, asset_id)
    rel = asset.meta.get("thumb") if asset else None
    if not rel:
        raise NotFound("Miniatura não encontrada")
    base = get_settings().data_dir.resolve()
    path = (base / str(rel)).resolve()
    if base not in path.parents or not path.is_file():
        raise NotFound("Miniatura não encontrada")
    return FileResponse(
        Path(path), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"}
    )
