from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.mcp import server
from canaisdark.mcp.context import current_user
from canaisdark.models import User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, users, videos

pytestmark = pytest.mark.anyio


@pytest.fixture
def setup(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ch = channels.create_channel(
        session,
        ChannelCreate(name="Canal", script_template="tom sombrio", visual_style_prompt="noir"),
    )
    assert ch.id is not None
    v = videos.create_video(session, ch.id, VideoCreate(topic="O caso X"), user_token[0])
    assert v.id is not None
    current_user.set(user_token[0])
    return v.id


async def call(name: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.mcp.call_tool(name, args or {})
    return result.structured_content if hasattr(result, "structured_content") else result


async def test_tools_registered() -> None:
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"claim_next_task", "save_script", "complete_task", "get_video", "get_script"} <= names
    assert "process_pending" in {p.name for p in await server.mcp.list_prompts()}


async def test_full_script_flow(setup: int) -> None:
    vid = setup
    pending = await call("list_pending_tasks")
    assert "generate_script" in str(pending)

    ctx = await call("claim_next_task")
    text = str(ctx)
    assert "tom sombrio" in text and "O caso X" in text and "how_to" in text
    task_id = _find_task_id(ctx)

    saved = await call(
        "save_script",
        {
            "video_id": vid,
            "task_id": task_id,
            "scenes": [{"text": "Cena um"}, {"text": "Cena dois"}],
        },
    )
    assert "'version': 1" in str(saved) or '"version": 1' in str(saved)
    await call("complete_task", {"task_id": task_id, "result_summary": "roteiro com 2 cenas"})

    script = await call("get_script", {"video_id": vid})
    assert "Cena dois" in str(script)
    video = await call("get_video", {"video_id": vid})
    assert "script_versions" in str(video)
    assert "Nenhuma tarefa" in str(await call("claim_next_task"))


def _find_task_id(ctx: Any) -> int:
    data = ctx.get("result", ctx) if isinstance(ctx, dict) else ctx
    return int(data["task"]["id"])


async def test_errors_are_explanatory(setup: int, session: Session) -> None:
    with pytest.raises(ToolError, match="não encontrado"):
        await call("get_video", {"video_id": 999})
    with pytest.raises(ToolError, match="entre 1 e 200"):
        await call("save_script", {"video_id": setup, "scenes": []})
    other = users.create_user(session, "Outro", "o@example.com")
    ctx = await call("claim_next_task")
    task_id = _find_task_id(ctx)
    current_user.set(other)
    with pytest.raises(ToolError, match="reservada por outro"):
        await call("complete_task", {"task_id": task_id, "result_summary": "x"})


async def test_job_tools(setup: int, session: Session) -> None:
    from canaisdark.models import JobType
    from canaisdark.services import jobs

    job = jobs.create_job(session, setup, JobType.TTS, {"x": 1})
    jobs.append_log(session, job.id or 0, "linha de log")
    status = await call("get_job_status", {"job_id": job.id})
    assert "queued" in str(status) and "linha de log" in str(status)
    assert str(job.id) in str(await call("list_jobs", {"video_id": setup}))
    with pytest.raises(ToolError, match="não encontrado"):
        await call("get_job_status", {"job_id": 999})
