"""Trilha sonora do vídeo: upload validado por conteúdo (nunca pela extensão) e reaproveitamento no canal."""

import hashlib
import shutil
from pathlib import Path

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, Video
from canaisdark.services import audio, channels, narration, storage, videos
from canaisdark.services.errors import InvalidState, NotFound


def detect_audio(data: bytes) -> str | None:
    """Extensão pela assinatura do arquivo: mp3, wav, ogg, m4a ou flac."""
    head = data[:16]
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"fLaC":
        return "flac"
    return None


def _folder(session: Session, video: Video) -> Path:
    channel = channels.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=True) / "music"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_music(session: Session, video_id: int) -> list[Asset]:
    return list(
        session.exec(
            select(Asset)
            .where(Asset.video_id == video_id, Asset.type == AssetType.MUSIC.value)
            .order_by(col(Asset.id).desc())
        )
    )


def list_channel_music(session: Session, video_id: int) -> list[Asset]:
    """Trilhas de OUTROS vídeos do mesmo canal (para reaproveitar)."""
    video = videos.get_video(session, video_id)
    ids = [
        v.id
        for v in session.exec(select(Video).where(Video.channel_id == video.channel_id))
        if v.id and v.id != video_id
    ]
    if not ids:
        return []
    return list(
        session.exec(
            select(Asset)
            .where(col(Asset.video_id).in_(ids), Asset.type == AssetType.MUSIC.value)
            .order_by(col(Asset.id).desc())
        )
    )


def save_upload(session: Session, video_id: int, data: bytes, filename: str) -> Asset:
    video = videos.get_video(session, video_id)
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not data:
        raise InvalidState("Arquivo vazio.")
    if len(data) > limit:
        raise InvalidState(
            f"Arquivo de {len(data) / 1048576:.0f} MB passa do limite de {get_settings().max_upload_mb} MB."
        )
    ext = detect_audio(data)
    if ext is None:
        raise InvalidState("Formato de áudio não suportado. Envie MP3, WAV, OGG, M4A ou FLAC.")
    digest = hashlib.sha1(data).hexdigest()[:8]
    path = _folder(session, video) / f"music_{digest}.{ext}"
    path.write_bytes(data)
    try:
        duration = audio.duration_seconds(path) if shutil.which("ffprobe") else None
    except Exception:  # noqa: BLE001 - duração é só informativa
        duration = None
    asset = Asset(
        video_id=video_id,
        type=AssetType.MUSIC.value,
        path=narration.relpath(path),
        meta={
            "original_name": Path(filename).name[:120],
            "bytes": len(data),
            "format": ext,
            "duration": round(duration, 1) if duration else None,
        },
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def reuse(session: Session, video_id: int, asset_id: int) -> Asset:
    """Copia uma trilha de outro vídeo do canal para este vídeo."""
    source = session.get(Asset, asset_id)
    allowed = {a.id for a in list_channel_music(session, video_id)}
    if source is None or source.type != AssetType.MUSIC.value or source.id not in allowed:
        raise NotFound("Trilha não encontrada neste canal.")
    src_path = get_settings().data_dir / source.path
    if not src_path.is_file():
        raise NotFound("O arquivo dessa trilha não está mais no disco.")
    return save_upload(
        session,
        video_id,
        src_path.read_bytes(),
        str(source.meta.get("original_name", src_path.name)),
    )


def remove(session: Session, video_id: int, asset_id: int) -> None:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.video_id != video_id or asset.type != AssetType.MUSIC.value:
        raise NotFound("Trilha não encontrada.")
    (get_settings().data_dir / asset.path).unlink(missing_ok=True)
    session.delete(asset)
    session.commit()
