"""Thumbnail do vídeo: validação pelo CONTEÚDO (Pillow), recompressão para caber em 2 MB, última vale."""

import hashlib
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType
from canaisdark.services import channels, narration, storage, videos
from canaisdark.services.errors import InvalidState

MAX_BYTES = 2_000_000  # limite do YouTube
MIN_WIDTH = 640
TARGET = (1280, 720)


def latest(session: Session, video_id: int) -> Asset | None:
    return session.exec(
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.THUMBNAIL.value)
        .order_by(col(Asset.id).desc())
    ).first()


def save_upload(session: Session, video_id: int, data: bytes) -> Asset:
    video = videos.get_video(session, video_id)
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not data:
        raise InvalidState("Arquivo vazio.")
    if len(data) > limit:
        raise InvalidState(f"Arquivo passa do limite de {get_settings().max_upload_mb} MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
            image = probe.convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise InvalidState("Não é uma imagem válida. Envie JPG ou PNG.") from e
    if fmt not in ("JPEG", "PNG"):
        raise InvalidState("Formato não suportado pelo YouTube. Envie JPG ou PNG.")
    width, height = image.size
    if width < MIN_WIDTH:
        raise InvalidState(
            f"A imagem tem {width}px de largura; o YouTube exige no mínimo {MIN_WIDTH}px."
        )
    warnings: list[str] = []
    if abs(width / height - 16 / 9) > 0.03:
        warnings.append("Proporção diferente de 16:9: o YouTube vai cortar ou deixar barras.")
    if width < TARGET[0]:
        warnings.append(f"Abaixo do recomendado ({TARGET[0]}×{TARGET[1]}).")
    if width > 1920:
        image = image.resize((1920, round(1920 * height / width)), Image.Resampling.LANCZOS)
    out = b""
    for quality in (92, 86, 80, 72, 64):
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) <= MAX_BYTES:
            break
    if len(out) > MAX_BYTES:
        raise InvalidState("Não consegui reduzir a imagem para menos de 2 MB. Envie uma menor.")
    channel = channels.get_channel(session, video.channel_id)
    folder: Path = storage.video_dir(channel.slug, video_id, create=True) / "thumbnail"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"thumb_{hashlib.sha1(out).hexdigest()[:8]}.jpg"
    path.write_bytes(out)
    for old in session.exec(
        select(Asset).where(Asset.video_id == video_id, Asset.type == AssetType.THUMBNAIL.value)
    ):
        if old.path != narration.relpath(path):
            (get_settings().data_dir / old.path).unlink(missing_ok=True)
        session.delete(old)
    asset = Asset(
        video_id=video_id,
        type=AssetType.THUMBNAIL.value,
        path=narration.relpath(path),
        meta={
            "width": image.size[0],
            "height": image.size[1],
            "bytes": len(out),
            "format": "jpeg",
            "warnings": warnings,
        },
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def remove(session: Session, video_id: int) -> None:
    asset = latest(session, video_id)
    if asset is None:
        return
    (get_settings().data_dir / asset.path).unlink(missing_ok=True)
    session.delete(asset)
    session.commit()
