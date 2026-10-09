from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.mcp import server
from canaisdark.mcp.context import current_user
from canaisdark.models import TaskType, User
from canaisdark.services import task_context, tasks, timeline
from tests.media import have_ffmpeg, setup_video

pytestmark = pytest.mark.anyio


async def call(name: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.mcp.call_tool(name, args or {})
    return result.structured_content if hasattr(result, "structured_content") else result


@pytest.fixture
def vid(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    current_user.set(user_token[0])
    return setup_video(session, user_token[0], [2.0, 3.0])["video_id"]


async def test_tools_registered() -> None:
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"get_timeline", "save_timeline", "start_render"} <= names


async def test_get_and_save_timeline(vid: int, session: Session) -> None:
    got = await call("get_timeline", {"video_id": vid})
    assert got["saved_version"] is None and len(got["timeline"]["scenes"]) == 2
    tl = got["timeline"]
    tl["scenes"][0]["motion"] = {"kind": "pan_left", "intensity": 0.07}
    tl["scenes"][1]["texts"] = [{"text": "1900", "position": "top"}]
    saved = await call("save_timeline", {"video_id": vid, "timeline": tl, "task_id": 5})
    assert saved["version"] == 1 and saved["task_id"] == 5
    _, now = timeline.latest(session, vid)  # type: ignore[misc]
    assert now.scenes[0].motion.kind == "pan_left"
    asset, _ = timeline.latest(session, vid)  # type: ignore[misc]
    assert asset.meta["source"] == "claude" and asset.meta["author"].startswith("claude:")
    tl["scenes"][0]["scene_index"] = 7
    with pytest.raises(ToolError, match="índices 0 a 1"):
        await call("save_timeline", {"video_id": vid, "timeline": tl})
    with pytest.raises(ToolError):
        await call(
            "save_timeline",
            {
                "video_id": vid,
                "timeline": {"scenes": [{"scene_index": 0, "motion": {"kind": "spin"}}]},
            },
        )


@pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg não instalado")
async def test_start_render_returns_job(vid: int) -> None:
    r = await call("start_render", {"video_id": vid, "quality": "preview"})
    assert r["job_id"] and r["status"] == "queued"
    with pytest.raises(ToolError, match="em andamento"):
        await call("start_render", {"video_id": vid})
    with pytest.raises(ToolError, match="Qualidade inválida"):
        await call("start_render", {"video_id": vid, "quality": "4k"})


def test_generate_timeline_task_context(
    vid: int, session: Session, user_token: tuple[User, str]
) -> None:
    tasks.create_task(session, vid, TaskType.GENERATE_TIMELINE, "mais calmo", user_token[0])
    claimed = tasks.claim_next(session, user_token[0], "generate_timeline")
    assert claimed
    ctx = task_context.build_task_context(session, claimed)
    assert "save_timeline" in ctx["how_to"] and "NÃO mude `music`" in ctx["how_to"]
    assert [s["index"] for s in ctx["scenes"]] == [0, 1] and ctx["scenes"][0]["length_s"] == 2.0
    assert ctx["scenes"][0]["asset"]["kind"] == "image"
    assert (
        len(ctx["default_timeline"]["scenes"]) == 2 and ctx["task"]["instructions"] == "mais calmo"
    )


def test_rest_timeline_and_render(
    client: TestClient, vid: int, user_token: tuple[User, str]
) -> None:
    h = {"Authorization": f"Bearer {user_token[1]}"}
    got = client.get(f"/api/videos/{vid}/timeline", headers=h).json()
    assert got["saved_version"] is None
    put = client.put(f"/api/videos/{vid}/timeline", headers=h, json={"timeline": got["timeline"]})
    assert put.status_code == 201 and put.json()["version"] == 1
    bad = client.put(
        f"/api/videos/{vid}/timeline",
        headers=h,
        json={"timeline": {"scenes": [{"scene_index": 9}]}},
    )
    assert bad.status_code == 422 and "índices 0 a 1" in str(bad.json())
    assert client.get(f"/api/videos/{vid}/render", headers=h).json() == {
        "preview": None,
        "final": None,
    }
    assert client.get(f"/api/videos/{vid}/timeline").status_code == 401
    if have_ffmpeg():
        r = client.post(f"/api/videos/{vid}/render", headers=h, json={"quality": "preview"})
        assert r.status_code == 202 and r.json()["type"] == "render"
