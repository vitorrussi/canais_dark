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
from canaisdark.services import task_context, tasks
from tests.media import have_ffmpeg, setup_video

pytestmark = pytest.mark.anyio

SEL = {
    "exact_text": "Cena número 1. Cena número 2.",
    "title": "Abertura",
    "hook_override": "O FAROL",
}


async def call(name: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.mcp.call_tool(name, args or {})
    return result.structured_content if hasattr(result, "structured_content") else result


@pytest.fixture
def vid(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    current_user.set(user_token[0])
    return setup_video(session, user_token[0], [10.0, 10.0, 10.0])["video_id"]


async def test_tools_registered() -> None:
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"get_shorts", "save_short_selections", "start_shorts"} <= names


async def test_save_and_get_shorts(vid: int) -> None:
    empty = await call("get_shorts", {"video_id": vid})
    assert empty["shorts"] == [] and empty["skipped"] is False
    saved = await call("save_short_selections", {"video_id": vid, "shorts": [SEL], "task_id": 3})
    assert saved["version"] == 1 and saved["task_id"] == 3
    assert saved["shorts"][0]["duration"] == pytest.approx(20.5, abs=1)
    got = await call("get_shorts", {"video_id": vid})
    assert got["shorts"][0]["title"] == "Abertura" and got["shorts"][0]["render"] is None
    bad = {"exact_text": "Isto não existe em nenhuma cena da narração", "title": "X"}
    with pytest.raises(ToolError, match="não foi encontrado"):
        await call("save_short_selections", {"video_id": vid, "shorts": [bad]})


def test_select_shorts_task_context(
    vid: int, session: Session, user_token: tuple[User, str]
) -> None:
    tasks.create_task(session, vid, TaskType.SELECT_SHORTS, "", user_token[0])
    claimed = tasks.claim_next(session, user_token[0], "select_shorts")
    assert claimed
    ctx = task_context.build_task_context(session, claimed)
    assert "save_short_selections" in ctx["how_to"] and "EXATAMENTE" in ctx["how_to"]
    assert [s["index"] for s in ctx["scenes"]] == [0, 1, 2]
    assert ctx["shorts_limits"]["max_seconds"] == 59.0


def test_rest_shorts(client: TestClient, vid: int, user_token: tuple[User, str]) -> None:
    h = {"Authorization": f"Bearer {user_token[1]}"}
    assert client.get(f"/api/videos/{vid}/shorts").status_code == 401
    assert client.get(f"/api/videos/{vid}/shorts", headers=h).json()["shorts"] == []
    put = client.put(f"/api/videos/{vid}/shorts", headers=h, json={"shorts": [SEL]})
    assert put.status_code == 201 and put.json()["count"] == 1
    bad = client.put(
        f"/api/videos/{vid}/shorts",
        headers=h,
        json={"shorts": [{"exact_text": "x" * 30, "title": "t"}]},
    )
    assert bad.status_code in (409, 422)
    skip = client.post(f"/api/videos/{vid}/shorts/skip", headers=h, json={"skipped": True})
    assert skip.json() == {"skipped": True}
    assert client.get(f"/api/videos/{vid}/shorts", headers=h).json()["skipped"] is True
    if have_ffmpeg():
        blocked = client.post(f"/api/videos/{vid}/shorts/render", headers=h, json={})
        assert blocked.status_code in (409, 422) and "dispensados" in blocked.text
        client.post(f"/api/videos/{vid}/shorts/skip", headers=h, json={"skipped": False})
        ok = client.post(f"/api/videos/{vid}/shorts/render", headers=h, json={"quality": "preview"})
        assert ok.status_code == 202 and ok.json()["type"] == "shorts"
