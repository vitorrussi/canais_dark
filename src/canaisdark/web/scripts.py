"""Editor de roteiro manual, salvamento e comparação de versões."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from canaisdark.schemas.script import SaveScriptRequest
from canaisdark.services import channels, script_diff, script_text, scripts, videos
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, render

router = APIRouter(include_in_schema=False)


@router.get("/videos/{video_id}/script/edit", response_class=HTMLResponse)
def edit_page(
    request: Request, user: WebUser, session: SessionDep, video_id: int, v: int | None = None
) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    versions = scripts.list_versions(session, video_id)
    base = next((x for x in versions if x.number == v), None) if v else None
    if base is None and versions:
        base = next((x for x in versions if x.approved), versions[0])
    scenes = scripts.scenes_of(session, base) if base else []
    data = {
        "videoId": video_id,
        "baseVersion": base.number if base else None,
        "scenes": [{"text": s.text, "notes": s.notes} for s in scenes]
        or [{"text": "", "notes": ""}],
        "targetSeconds": channel.target_duration_minutes * 60,
        "wpm": script_text.WORDS_PER_MINUTE,
        "maxScenes": 200,
        "maxSceneChars": 5000,
    }
    return render(
        request,
        "script_editor.html",
        user,
        video=video,
        channel=channel,
        base=base,
        editor_data=data,
        crumbs=[
            ("Canais", "/"),
            (channel.name, f"/channels/{channel.slug}"),
            (f"Vídeo #{video.id}", f"/videos/{video.id}"),
        ],
    )


@router.post("/videos/{video_id}/script/save")
def save(
    request: Request, user: WebUser, session: SessionDep, video_id: int, data: SaveScriptRequest
) -> Response:
    """Chamado pelo editor (JSON, cookie de sessão). Cria nova versão e agenda o toast."""
    try:
        version = scripts.save_user_script(session, video_id, data.scenes, user, data.approve)
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    msg = f"Roteiro salvo como v{version.number}" + (" e aprovado" if data.approve else "")
    request.session["flash"] = {"kind": "success", "msg": msg}
    return JSONResponse(
        {"ok": True, "version": version.number, "url": f"/videos/{video_id}?v={version.number}"}
    )


@router.get("/videos/{video_id}/diff", response_class=HTMLResponse)
def diff_page(
    request: Request, user: WebUser, session: SessionDep, video_id: int, a: int, b: int
) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    old_v, old_scenes = scripts.get_script(session, video_id, a)
    new_v, new_scenes = scripts.get_script(session, video_id, b)
    rows = script_diff.diff_scenes([s.text for s in old_scenes], [s.text for s in new_scenes])
    return render(
        request,
        "script_diff.html",
        user,
        video=video,
        channel=channel,
        old=old_v,
        new=new_v,
        rows=rows,
        summary=script_diff.summarize(rows),
        versions=scripts.list_versions(session, video_id),
        crumbs=[
            ("Canais", "/"),
            (channel.name, f"/channels/{channel.slug}"),
            (f"Vídeo #{video.id}", f"/videos/{video.id}"),
        ],
    )
