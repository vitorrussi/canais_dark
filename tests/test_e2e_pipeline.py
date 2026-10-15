"""Pipeline completo, de ponta a ponta, com provedores FALSOS e sem rodar o ffmpeg.

Cada etapa usa a mesma interface que as pessoas/Claude usam (site, REST, tools MCP, worker). O render
de vídeo (ffmpeg) fica de fora de propósito: o resultado é registrado como arquivo de teste.
"""

import io
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import drive as gd
from canaisdark.integrations import youtube as yt
from canaisdark.integrations.elevenlabs import FakeElevenLabs
from canaisdark.mcp import server
from canaisdark.mcp.context import current_user
from canaisdark.models import Asset, AssetType, User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import (
    channels,
    drive,
    jobs,
    metadata,
    narration,
    scripts,
    thumbnail,
    timeline,
    videos,
)
from canaisdark.services import youtube as yt_service
from canaisdark.worker.runner import Worker

pytestmark = [pytest.mark.anyio, pytest.mark.e2e]

SCENES = [
    "Era uma noite escura em 1900 e o farol estava aceso.",
    "Três homens sumiram sem deixar nenhum rastro no cais.",
    "Ninguém soube explicar o que aconteceu naquela noite.",
]


async def tool(name: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.mcp.call_tool(name, args or {})
    return result.structured_content if hasattr(result, "structured_content") else result


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), (30, 60, 120)).save(buf, "PNG")
    return buf.getvalue()


def drain(worker: Worker) -> int:
    n = 0
    while worker.run_once():
        n += 1
    return n


async def test_full_pipeline_with_fake_providers(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, token = user_token
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "KEY")
    monkeypatch.setattr(settings, "youtube_fake", True)
    monkeypatch.setattr(settings, "drive_fake", True)
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    fake_tts = FakeElevenLabs()
    fake_yt, fake_drive = yt.FakeYouTube(), gd.FakeDrive()
    monkeypatch.setattr(yt, "_demo", fake_yt)
    monkeypatch.setattr(gd, "_demo", fake_drive)
    worker = Worker(make_client=lambda: fake_tts, poll_seconds=0)  # type: ignore[arg-type,return-value]
    current_user.set(user)
    assert client.post("/login", data={"token": token}).status_code in (200, 303)

    # 1) canal e vídeo (criar o vídeo enfileira a tarefa de roteiro para o Claude)
    channel = channels.create_channel(
        session, ChannelCreate(name="Canal E2E", elevenlabs_voice_id="voz123")
    )
    assert channel.id
    video = videos.create_video(session, channel.id, VideoCreate(topic="O farol"), user)
    assert video.id
    vid = video.id

    # 2) Claude: reserva a tarefa, salva o roteiro e conclui (tools MCP)
    claimed = await tool("claim_next_task", {"video_id": vid})
    assert claimed["task"]["type"] == "generate_script"
    saved = await tool(
        "save_script",
        {
            "video_id": vid,
            "scenes": [{"text": t} for t in SCENES],
            "task_id": claimed["task"]["id"],
        },
    )
    assert saved["version"] == 1
    await tool("complete_task", {"task_id": claimed["task"]["id"], "result_summary": "ok"})

    # 3) pessoa: aprova o roteiro pelo site
    version = scripts.list_versions(session, vid)[0]
    assert client.post(f"/scripts/{version.id}/approve", follow_redirects=False).status_code == 303
    session.expire_all()
    assert videos.get_video(session, vid).approved_script_version_id == version.id

    # 4) narração (ElevenLabs falso) + alinhamento pelo worker
    narration.start(session, vid, user)
    assert drain(worker) >= 1
    session.expire_all()
    audio_asset = narration.latest_asset(session, vid, AssetType.AUDIO, "narration")
    assert audio_asset is not None and narration.asset_path(audio_asset).exists()
    assert fake_tts.tts_calls and fake_tts.tts_calls[0]["voice_id"] == "voz123"
    assert all(s.start_s is not None for s in scripts.scenes_of(session, version))

    # 5) imagens das cenas pelo site (validação por conteúdo)
    for i in range(len(SCENES)):
        res = client.post(
            f"/videos/{vid}/scenes/{i}/upload", files={"file": (f"c{i}.png", png(), "image/png")}
        )
        assert res.status_code == 200 and res.json()["ok"] is True

    # 6) timeline: Claude lê a padrão e salva uma versão ajustada
    got = await tool("get_timeline", {"video_id": vid})
    tl = got["timeline"]
    tl["scenes"][0]["motion"] = {"kind": "pan_left", "intensity": 0.07}
    assert (await tool("save_timeline", {"video_id": vid, "timeline": tl}))["version"] == 1

    # 7) render final (ffmpeg fica de fora: registramos um arquivo de teste) e aprovação humana
    final = tmp_path / "files" / "final.mp4"
    final.write_bytes(b"x" * 4000)
    tl_asset, _ = timeline.ensure_timeline(session, vid, "e2e")
    session.add(
        Asset(
            video_id=vid,
            type=AssetType.RENDER.value,
            path="final.mp4",
            meta={
                "kind": "final",
                "duration": 8.0,
                "bytes": 4000,
                "timeline_asset_id": tl_asset.id,
                "inputs_hash": timeline.inputs_hash(session, videos.get_video(session, vid)),
                "partial": False,
            },
        )
    )
    session.commit()
    assert client.post(f"/videos/{vid}/render/approve", follow_redirects=False).status_code == 303
    session.expire_all()

    # 8) metadados: Claude propõe, a pessoa aplica
    prop = await tool(
        "save_metadata",
        {
            "video_id": vid,
            "title": "O mistério do farol",
            "description": "Três homens sumiram.",
            "tags": ["farol", "mistério"],
            "thumbnail_ideas": ["farol à noite"],
        },
    )
    assert prop["version"] == 1
    metadata.apply(session, vid, user)
    session.expire_all()
    assert videos.get_video(session, vid).title == "O mistério do farol"

    # 9) thumbnail pelo Claude: pacote → envio com o link assinado (sem sessão)
    pkg = await tool("get_thumbnail_package", {"video_id": vid})
    assert pkg["thumbnail_ideas"] == ["farol à noite"] and len(pkg["files"]) == 3
    anon = TestClient(client.app)
    up = anon.post("/x/" + pkg["upload"]["url"].split("/x/")[1], content=png())
    assert up.status_code == 200 and up.json()["status"] == "ok"
    session.expire_all()
    assert thumbnail.latest(session, vid) is not None

    # 10) YouTube (sempre privado) e Drive, ambos falsos, pelo worker
    state = yt_service.make_state(channel.id, user.id or 0)
    yt_service.complete_connection(session, "fake-code", state, user)
    job = yt_service.start_upload(session, vid, user)
    drain(worker)
    session.expire_all()
    assert jobs.get_job(session, job.id or 0).status == "done"
    assert fake_yt.uploads and all(
        u["status"]["privacyStatus"] == "private" for u in fake_yt.uploads
    )

    dstate = drive.make_state(channel.id, user.id or 0)
    drive.complete_connection(session, "fake-code", dstate, user)
    djob = drive.start_sync(session, vid, user)
    drain(worker)
    session.expire_all()
    assert jobs.get_job(session, djob.id or 0).status == "done"

    # 11) exportar o vídeo e abrir as páginas principais
    exported = client.get(f"/videos/{vid}/export")
    assert exported.status_code == 200 and exported.json()["format"] == "canaisdark.video"
    for path in (
        f"/videos/{vid}",
        f"/videos/{vid}/scenes",
        f"/videos/{vid}/timeline",
        "/novidades",
    ):
        assert client.get(path).status_code == 200, path
