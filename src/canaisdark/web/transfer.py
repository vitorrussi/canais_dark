"""Rotas web de baixar/exportar/importar (roteiro, vídeo e canal)."""

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from canaisdark.models import TaskType
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.transfer import ChannelBundle
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import (
    channels,
    script_formats,
    scripts,
    tasks,
    transfer,
    videos,
)
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, error_text, render

router = APIRouter(include_in_schema=False)

RULES = script_formats.SPLIT_RULES


def _attachment(filename: str, mime: str, content: str) -> Response:
    return Response(
        content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _scenes_from_form(
    paste: str, file: UploadFile | None, rule: str, max_chars: int
) -> list[SceneIn]:
    data = await file.read() if file is not None and file.filename else None
    return script_formats.parse_input(paste, file.filename if file else None, data, rule, max_chars)


def _crumbs(channel_name: str, slug: str, video_id: int | None = None) -> list[tuple[str, str]]:
    crumbs = [("Canais", "/"), (channel_name, f"/channels/{slug}")]
    if video_id:
        crumbs.append((f"Vídeo #{video_id}", f"/videos/{video_id}"))
    return crumbs


# ----- Baixar / exportar -----


@router.get("/scripts/{version_id}/download")
def download_script(
    user: WebUser, session: SessionDep, version_id: int, format: str = "txt"
) -> Response:
    version = scripts.get_version(session, version_id)
    video = videos.get_video(session, version.video_id)
    channel = channels.get_channel(session, video.channel_id)
    d = script_formats.render_script(
        format,
        script_formats.default_title(video),
        version,
        scripts.scenes_of(session, version),
        channel,
        video,
    )
    return _attachment(d.filename, d.mime, d.content)


@router.get("/videos/{video_id}/export")
def export_video(user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    content = transfer.dumps(transfer.export_video(session, video_id))
    return _attachment(f"{channel.slug}-video-{video_id}.json", "application/json", content)


@router.get("/channels/{slug}/export")
def export_channel(user: WebUser, session: SessionDep, slug: str, videos: int = 1) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    content = transfer.dumps(transfer.export_channel(session, channel.id, bool(videos)))
    suffix = "" if videos else "-config"
    return _attachment(f"{slug}{suffix}.json", "application/json", content)


# ----- Importar roteiro em vídeo existente -----


@router.get("/videos/{video_id}/script/import", response_class=HTMLResponse)
def import_script_page(
    request: Request, user: WebUser, session: SessionDep, video_id: int
) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    return render(
        request,
        "script_import.html",
        user,
        video=video,
        rules=RULES,
        crumbs=_crumbs(channel.name, channel.slug, video_id),
    )


@router.post("/videos/{video_id}/script/import/preview", response_class=HTMLResponse)
async def import_script_preview(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    paste: Annotated[str, Form()] = "",
    rule: Annotated[str, Form()] = "auto",
    max_chars: Annotated[int, Form()] = 400,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    return await _preview(request, user, paste, file, rule, max_chars)


async def _preview(
    request: Request, user: object, paste: str, file: UploadFile | None, rule: str, max_chars: int
) -> Response:
    try:
        scenes = await _scenes_from_form(paste, file, rule, max_chars)
        used = (
            script_formats.detect_rule(paste)
            if rule == "auto" and not (file and file.filename)
            else rule
        )
        return render(
            request,
            "_import_preview.html",
            user,  # type: ignore[arg-type]
            scenes=scenes,
            summary=script_formats.summarize(scenes),
            used_rule=RULES.get(used, used),
        )
    except (DomainError, ValidationError) as e:
        return render(request, "_import_preview.html", user, error=error_text(e))  # type: ignore[arg-type]


@router.post("/videos/{video_id}/script/import")
async def import_script(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    paste: Annotated[str, Form()] = "",
    rule: Annotated[str, Form()] = "auto",
    max_chars: Annotated[int, Form()] = 400,
    approve: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    try:
        scenes = await _scenes_from_form(paste, file, rule, max_chars)
        version = scripts.save_user_script(session, video_id, scenes, user, bool(approve))
    except (DomainError, ValidationError) as e:
        return back(
            request,
            f"/videos/{video_id}/script/import",
            error=e if isinstance(e, DomainError) else ValueError(error_text(e)),
        )
    return back(
        request,
        f"/videos/{video_id}?v={version.number}",
        ok=f"Roteiro importado como v{version.number} ({len(scenes)} cenas)",
    )


# ----- Novo vídeo com roteiro manual -----


@router.get("/channels/{slug}/videos/manual", response_class=HTMLResponse)
def manual_video_page(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    return render(
        request,
        "video_manual.html",
        user,
        channel=channel,
        rules=RULES,
        crumbs=_crumbs(channel.name, channel.slug),
    )


@router.post("/channels/{slug}/videos/manual/preview", response_class=HTMLResponse)
async def manual_video_preview(
    request: Request,
    user: WebUser,
    slug: str,
    paste: Annotated[str, Form()] = "",
    rule: Annotated[str, Form()] = "auto",
    max_chars: Annotated[int, Form()] = 400,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    return await _preview(request, user, paste, file, rule, max_chars)


@router.post("/channels/{slug}/videos/manual")
async def manual_video(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    title: Annotated[str, Form()],
    topic: Annotated[str, Form()] = "",
    paste: Annotated[str, Form()] = "",
    rule: Annotated[str, Form()] = "auto",
    max_chars: Annotated[int, Form()] = 400,
    approve: Annotated[str | None, Form()] = None,
    ask_claude: Annotated[str | None, Form()] = None,
    claude_notes: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    try:
        scenes = await _scenes_from_form(paste, file, rule, max_chars)
        title = title.strip()
        video = videos.create_video(
            session,
            channel.id,
            VideoCreate(topic=topic.strip() or title, title=title),
            user,
            enqueue_script=False,
        )
        assert video.id is not None
        version = scripts.save_user_script(session, video.id, scenes, user, bool(approve))
        if ask_claude:
            note = (
                claude_notes.strip()
                or "Revise o roteiro enviado: melhore ritmo e clareza sem mudar os fatos."
            )
            tasks.create_task(session, video.id, TaskType.GENERATE_SCRIPT, note, user)
    except (DomainError, ValidationError) as e:
        return render(
            request,
            "video_manual.html",
            user,
            status=422,
            channel=channel,
            rules=RULES,
            error=error_text(e),
            form={"title": title, "topic": topic, "paste": paste},
            crumbs=_crumbs(channel.name, channel.slug),
        )
    return back(
        request,
        f"/videos/{video.id}",
        ok=f"Vídeo criado com roteiro v{version.number} ({len(scenes)} cenas)",
    )


# ----- Importar vídeo/canal de arquivo exportado -----


def _bundle_ctx(session: SessionDep) -> dict[str, Any]:
    return {"channel_list": channels.list_channels(session)}


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, user: WebUser, session: SessionDep) -> Response:
    return render(
        request, "import_bundle.html", user, crumbs=[("Canais", "/")], **_bundle_ctx(session)
    )


@router.post("/import/inspect", response_class=HTMLResponse)
async def import_inspect(
    request: Request,
    user: WebUser,
    session: SessionDep,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    try:
        if file is None or not file.filename:
            raise DomainError("Escolha um arquivo .json exportado por este sistema")
        bundle = transfer.parse_bundle(script_formats.decode_upload(await file.read()))
    except (DomainError, ValidationError) as e:
        return render(request, "_bundle_info.html", user, error=error_text(e))
    kind = bundle.format
    info: dict[str, object] = {"kind": kind}
    if isinstance(bundle, ChannelBundle):
        info.update(
            name=bundle.channel.name,
            videos=len(bundle.videos),
            scripts=sum(len(v.scripts) for v in bundle.videos),
        )
    else:
        info.update(name=bundle.video.title or bundle.video.topic[:60], scripts=len(bundle.scripts))
    return render(request, "_bundle_info.html", user, info=info, **_bundle_ctx(session))


@router.post("/import")
async def import_bundle(
    request: Request,
    user: WebUser,
    session: SessionDep,
    name: Annotated[str, Form()] = "",
    target_channel: Annotated[str, Form()] = "",
    include_videos: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    try:
        if file is None or not file.filename:
            raise DomainError("Escolha um arquivo .json exportado por este sistema")
        bundle = transfer.parse_bundle(script_formats.decode_upload(await file.read()))
        if isinstance(bundle, ChannelBundle):
            channel = transfer.import_channel(
                session, bundle, user, name or None, bool(include_videos)
            )
            return back(
                request, f"/channels/{channel.slug}", ok=f"Canal “{channel.name}” importado"
            )
        if not target_channel:
            raise DomainError("Escolha o canal de destino do vídeo")
        dest = channels.get_channel_by_slug(session, target_channel)
        assert dest.id is not None
        video = transfer.import_video(session, dest.id, bundle, user, title=name or None)
        return back(request, f"/videos/{video.id}", ok="Vídeo importado")
    except (DomainError, ValidationError) as e:
        return back(
            request, "/import", error=e if isinstance(e, DomainError) else ValueError(error_text(e))
        )
