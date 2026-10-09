import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.mcp import server
from canaisdark.mcp.context import current_user
from canaisdark.models import TaskType, User
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.services import metadata, task_context, tasks, thumbnail, videos
from canaisdark.services.errors import InvalidState
from tests.media import setup_video


@pytest.fixture
def vid(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    current_user.set(user_token[0])
    return setup_video(session, user_token[0], [2.0, 3.0])["video_id"]


def png(size: tuple[int, int], fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 60, 90)).save(buf, fmt)
    return buf.getvalue()


def test_schema_normalizes_and_rejects() -> None:
    m = MetadataIn(
        title="  O farol   que sumiu ",
        tags=["Farol", "farol", "#mistério, noturno", "", "história real"],
        thumbnail_ideas=[" farol com luz ", ""],
    )
    assert m.title == "O farol que sumiu"
    assert m.tags == ["Farol", "mistério noturno", "história real"]
    assert m.thumbnail_ideas == ["farol com luz"]
    with pytest.raises(ValidationError, match="< ou >"):
        MetadataIn(title="a <b>")
    with pytest.raises(ValidationError):
        MetadataIn(title="x" * 101)
    with pytest.raises(ValidationError, match="500"):
        MetadataIn(title="ok", tags=[f"tag numero {i:02d} " + "x" * 20 for i in range(20)])
    with pytest.raises(ValidationError, match="5000"):
        MetadataIn(title="ok", description="é" * 2600)


def test_proposal_versions_and_apply(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    user = user_token[0]
    with pytest.raises(InvalidState, match="Ainda não há proposta"):
        metadata.apply(session, vid, user)
    a1 = metadata.save_proposal(
        session, vid, MetadataIn(title="V1", description="d1", tags=["a"]), "claude", "claude:1"
    )
    a2 = metadata.save_proposal(
        session, vid, MetadataIn(title="V2", description="d2", tags=["b", "c"]), "user", "user:1"
    )
    assert (a1.meta["number"], a2.meta["number"]) == (1, 2)
    video = videos.get_video(session, vid)
    assert video.title == "" and metadata.problems(video)  # nada muda sozinho
    out = metadata.apply(session, vid, user)
    assert (out.title, out.description, out.tags) == ("V2", "d2", ["b", "c"])
    assert metadata.problems(out) == []
    session.refresh(a2)
    assert a2.meta["applied"] is True and a2.meta["applied_by"] == user.id
    edited = metadata.apply(session, vid, user, edits=MetadataIn(title="V3", description="d3"))
    assert edited.title == "V3" and edited.tags == []


def test_thumbnail_validation_and_replace(session: Session, vid: int) -> None:
    with pytest.raises(InvalidState, match="imagem válida"):
        thumbnail.save_upload(session, vid, b"nao e imagem")
    with pytest.raises(InvalidState, match="mínimo 640"):
        thumbnail.save_upload(session, vid, png((320, 180)))
    first = thumbnail.save_upload(session, vid, png((1280, 720)))
    assert first.meta["format"] == "jpeg" and first.meta["warnings"] == []
    path = get_settings().data_dir / first.path
    assert path.is_file()
    second = thumbnail.save_upload(session, vid, png((1000, 1000)))
    assert any("16:9" in w for w in second.meta["warnings"])
    assert not path.exists() and thumbnail.latest(session, vid).id == second.id  # type: ignore[union-attr]
    thumbnail.remove(session, vid)
    assert thumbnail.latest(session, vid) is None


def test_thumbnail_recompresses_to_2mb(session: Session, vid: int) -> None:
    import random

    rnd = random.Random(1)
    img = Image.frombytes(
        "RGB", (1920, 1080), rnd.randbytes(1920 * 1080 * 3)
    )  # ruído: não comprime
    buf = io.BytesIO()
    img.save(buf, "PNG")
    try:
        asset = thumbnail.save_upload(session, vid, buf.getvalue())
    except InvalidState as e:
        assert "2 MB" in str(e)  # ruído puro pode não caber: a mensagem é clara
    else:
        assert asset.meta["bytes"] <= 2_000_000


pytestmark_anyio = pytest.mark.anyio


async def call(name: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.mcp.call_tool(name, args or {})
    return result.structured_content if hasattr(result, "structured_content") else result


@pytest.mark.anyio
async def test_mcp_save_and_get_metadata(vid: int) -> None:
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"get_metadata", "save_metadata"} <= names
    saved = await call(
        "save_metadata",
        {
            "video_id": vid,
            "title": "O farol",
            "description": "Texto",
            "tags": ["farol"],
            "thumbnail_ideas": ["farol à noite"],
            "task_id": 9,
        },
    )
    assert saved == {"version": 1, "applied": False, "task_id": 9}
    got = await call("get_metadata", {"video_id": vid})
    assert got["proposal"]["title"] == "O farol" and got["proposal"]["applied"] is False
    assert got["problems"]  # vídeo ainda sem título aplicado
    with pytest.raises(Exception, match="< ou >"):
        await call("save_metadata", {"video_id": vid, "title": "<x>"})
    assert "youtube" not in " ".join(sorted(names)) and "upload" not in " ".join(sorted(names))


def test_metadata_task_context(session: Session, vid: int, user_token: tuple[User, str]) -> None:
    tasks.create_task(session, vid, TaskType.GENERATE_METADATA, "", user_token[0])
    claimed = tasks.claim_next(session, user_token[0], "generate_metadata")
    assert claimed
    ctx = task_context.build_task_context(session, claimed)
    assert "save_metadata" in ctx["how_to"] and ctx["metadata"]["limits"]["title_max"] == 100
    assert [s["index"] for s in ctx["scenes"]] == [0, 1]


def test_rest_metadata_and_thumbnail(
    client: TestClient, vid: int, user_token: tuple[User, str]
) -> None:
    h = {"Authorization": f"Bearer {user_token[1]}"}
    assert client.get(f"/api/videos/{vid}/metadata").status_code == 401
    body = {"title": "Título", "description": "Desc", "tags": ["x"]}
    put = client.put(f"/api/videos/{vid}/metadata?apply=true", headers=h, json=body)
    assert put.status_code == 201 and put.json() == {"version": 1, "applied": True}
    got = client.get(f"/api/videos/{vid}/metadata", headers=h).json()
    assert got["current"]["title"] == "Título" and got["problems"] == []
    bad = client.put(f"/api/videos/{vid}/metadata", headers=h, json={"title": "a<b"})
    assert bad.status_code == 422
    up = client.put(
        f"/api/videos/{vid}/thumbnail",
        headers=h,
        files={"file": ("t.png", png((1280, 720)), "image/png")},
    )
    assert up.status_code == 201 and up.json()["width"] == 1280
    assert (
        client.get(f"/api/videos/{vid}/metadata", headers=h).json()["thumbnail"]["format"] == "jpeg"
    )
