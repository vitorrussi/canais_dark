"""Metadados do YouTube: proposta versionada (Claude ou pessoa) e aplicação ao vídeo.

A proposta nunca altera o vídeo sozinha: `apply` é um ato humano (mesmo princípio da aprovação do roteiro).
"""

import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, User, Video
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.schemas.video import VideoUpdate
from canaisdark.services import channels, narration, storage, videos
from canaisdark.services.errors import InvalidState, NotFound


def _folder(session: Session, video: Video) -> Path:
    channel = channels.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=True) / "metadata"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_versions(session: Session, video_id: int) -> list[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.METADATA.value)
        .order_by(col(Asset.id))
    )
    return list(session.exec(stmt))


def load(asset: Asset) -> MetadataIn:
    return MetadataIn.model_validate_json(
        (get_settings().data_dir / asset.path).read_text(encoding="utf-8")
    )


def latest(session: Session, video_id: int) -> tuple[Asset, MetadataIn] | None:
    versions = list_versions(session, video_id)
    if not versions:
        return None
    return versions[-1], load(versions[-1])


def save_proposal(
    session: Session, video_id: int, data: MetadataIn, source: str, author: str
) -> Asset:
    """Salva SEMPRE uma versão nova (nunca sobrescreve). `source`: claude | user."""
    video = videos.get_video(session, video_id)
    number = len(list_versions(session, video_id)) + 1
    path = _folder(session, video) / f"metadata_v{number}.json"
    path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
    asset = Asset(
        video_id=video_id,
        type=AssetType.METADATA.value,
        path=narration.relpath(path),
        meta={"source": source, "author": author, "number": number, "applied": False},
        based_on_script_version_id=video.approved_script_version_id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def apply(
    session: Session,
    video_id: int,
    actor: User,
    asset_id: int | None = None,
    edits: MetadataIn | None = None,
) -> Video:
    """Copia a proposta (ou o texto já editado pela pessoa) para título/descrição/tags do vídeo."""
    found: tuple[Asset, MetadataIn] | None
    if asset_id is not None:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.video_id != video_id or asset.type != AssetType.METADATA.value:
            raise NotFound("Proposta de metadados não encontrada.")
        found = (asset, load(asset))
    else:
        found = latest(session, video_id)
    if edits is None and found is None:
        raise InvalidState("Ainda não há proposta de metadados para aplicar.")
    data = edits if edits is not None else found[1]  # type: ignore[index]
    video = videos.update_video(
        session,
        video_id,
        VideoUpdate(title=data.title, description=data.description, tags=data.tags),
    )
    if found is not None:
        found[0].meta = {
            **found[0].meta,
            "applied": True,
            "applied_by": actor.id,
            "applied_at": int(time.time()),
        }
        session.add(found[0])
        session.commit()
    return video


def current(video: Video) -> dict[str, Any]:
    return {"title": video.title, "description": video.description, "tags": list(video.tags)}


def problems(video: Video) -> list[str]:
    """O que falta no vídeo para ser enviado ao YouTube."""
    out: list[str] = []
    if not video.title.strip():
        out.append("Falta o título do vídeo.")
    elif len(video.title) > 100 or "<" in video.title or ">" in video.title:
        out.append("O título tem mais de 100 caracteres ou contém < >.")
    if not video.description.strip():
        out.append("Falta a descrição do vídeo.")
    return out
