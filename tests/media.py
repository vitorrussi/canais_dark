"""Ajudantes de teste para vídeos com narração, tempos e imagens (sem rede)."""

import shutil
import subprocess
from pathlib import Path

from PIL import Image
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, narration, scripts, storage, videos

COLORS = [(200, 40, 40), (40, 160, 60), (40, 80, 200), (220, 200, 40), (160, 40, 200)]


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_audio(path: Path, seconds: float) -> None:
    """MP3 de silêncio com ffmpeg real (só para testes de render)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            str(seconds),
            "-q:a",
            "9",
            str(path),
        ],
        check=True,
    )


def setup_video(
    session: Session,
    user: User,
    durations: list[float],
    images: bool = True,
    real_audio: bool = False,
    size: tuple[int, int] = (320, 180),
) -> dict[str, int]:
    """Canal + vídeo com roteiro aprovado, áudio, tempos por cena e (opcional) uma imagem por cena."""
    ch = channels.create_channel(session, ChannelCreate(name="Canal Teste"))
    assert ch.id
    video = videos.create_video(session, ch.id, VideoCreate(topic="t"), user)
    assert video.id
    version = scripts.save_user_script(
        session,
        video.id,
        [SceneIn(text=f"Cena número {i + 1}.") for i in range(len(durations))],
        user,
        approve=True,
    )
    base = storage.video_dir(ch.slug, video.id, create=True)
    total = sum(durations)
    audio_path = base / "audio" / "narration_v1.mp3"
    if real_audio:
        make_audio(audio_path, total)
    else:
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"ID3fake")
    audio = Asset(
        video_id=video.id,
        type=AssetType.AUDIO.value,
        path=narration.relpath(audio_path),
        meta={"kind": "narration", "duration": total},
        based_on_script_version_id=version.id,
    )
    session.add(audio)
    t = 0.0
    scenes = scripts.scenes_of(session, version)
    for scene, d in zip(scenes, durations, strict=True):
        scene.start_s, scene.end_s = round(t, 3), round(t + d, 3)
        session.add(scene)
        t += d
        if images:
            img = base / "images" / f"s{scene.index:03d}_main.jpg"
            img.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", size, COLORS[scene.index % len(COLORS)]).save(img, quality=90)
            session.add(
                Asset(
                    video_id=video.id,
                    scene_id=scene.id,
                    type=AssetType.IMAGE.value,
                    path=narration.relpath(img),
                    meta={"variant": "main", "width": size[0], "height": size[1]},
                )
            )
    session.commit()
    assert get_settings().data_dir
    return {"video_id": video.id, "channel_id": ch.id, "version_id": version.id or 0}
