"""Modo sem MCP no site: copiar o prompt completo e colar a resposta do Claude."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from canaisdark.services import offline_tasks, videos
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, render

router = APIRouter(include_in_schema=False)

_AFTER: dict[str, str] = {
    "generate_script": "/videos/{id}",
    "generate_prompts": "/videos/{id}/scenes",
    "generate_timeline": "/videos/{id}/timeline",
    "select_shorts": "/videos/{id}#shorts-card",
    "generate_metadata": "/videos/{id}#youtube-card",
}


def _page(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    task_type: str,
    answer: str = "",
    error: str | None = None,
    status: int = 200,
) -> Response:
    video = videos.get_video(session, video_id)
    prompt = offline_tasks.build_prompt(session, video_id, task_type, user)
    return render(
        request,
        "offline_task.html",
        user,
        status=status,
        video=video,
        task_type=task_type,
        label=offline_tasks.LABELS[task_type],
        prompt=prompt,
        answer=answer,
        error=error,
    )


@router.get("/videos/{video_id}/claude/{task_type}", response_class=HTMLResponse)
def offline_page(
    request: Request, user: WebUser, session: SessionDep, video_id: int, task_type: str
) -> Response:
    return _page(request, user, session, video_id, task_type)


@router.post("/videos/{video_id}/claude/{task_type}")
def offline_apply(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    task_type: str,
    answer: str = Form(""),
) -> Response:
    try:
        result = offline_tasks.apply_result(session, video_id, task_type, user, answer)
    except DomainError as e:
        return _page(
            request, user, session, video_id, task_type, answer=answer, error=str(e), status=422
        )
    missing = result.get("missing_scene_indexes")
    note = f" Faltam prompts nas cenas {missing}." if missing else ""
    return back(
        request,
        _AFTER[task_type].format(id=video_id),
        ok=f"Resposta aplicada: {result['summary']}.{note}",
    )
