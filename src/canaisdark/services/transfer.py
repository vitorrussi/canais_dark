"""Exportar/importar vídeos e canais (somente dados; sem mídia)."""

import json
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.models import Channel, ScriptVersion, User, Video
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.transfer import (
    CURRENT_VERSION,
    FORMAT_CHANNEL,
    FORMAT_VIDEO,
    ChannelBundle,
    ScriptBundle,
    VideoBundle,
    VideoData,
)
from canaisdark.schemas.video import VideoCreate, VideoUpdate
from canaisdark.services import channels, scripts, videos
from canaisdark.services.errors import Conflict, InvalidState

_CHANNEL_FIELDS = tuple(ChannelCreate.model_fields)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def export_video(session: Session, video_id: int) -> VideoBundle:
    video = videos.get_video(session, video_id)
    bundles = []
    for version in sorted(scripts.list_versions(session, video_id), key=lambda v: v.number):
        bundles.append(_script_bundle(session, version))
    return VideoBundle(
        exported_at=_now(),
        video=VideoData(
            topic=video.topic,
            title=video.title,
            description=video.description,
            tags=video.tags,
            language=video.language,
        ),
        scripts=bundles,
    )


def _script_bundle(session: Session, version: ScriptVersion) -> ScriptBundle:
    return ScriptBundle(
        number=version.number,
        author=version.author,
        approved=version.approved,
        created_at=version.created_at.isoformat(timespec="seconds"),
        scenes=[SceneIn(text=s.text, notes=s.notes) for s in scripts.scenes_of(session, version)],
    )


def export_channel(session: Session, channel_id: int, include_videos: bool = True) -> ChannelBundle:
    channel = channels.get_channel(session, channel_id)
    data = ChannelCreate(**{k: getattr(channel, k) for k in _CHANNEL_FIELDS})
    vids = []
    if include_videos:
        vids = [
            export_video(session, v.id)
            for v in reversed(videos.list_videos(session, channel_id))
            if v.id is not None
        ]
    return ChannelBundle(exported_at=_now(), channel=data, videos=vids)


def dumps(bundle: VideoBundle | ChannelBundle) -> str:
    return json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2)


def parse_bundle(raw: str) -> VideoBundle | ChannelBundle:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InvalidState("O arquivo não é um JSON válido") from e
    kind = data.get("format") if isinstance(data, dict) else None
    if kind not in (FORMAT_VIDEO, FORMAT_CHANNEL):
        raise InvalidState(
            "Arquivo não reconhecido. Use um arquivo exportado por este sistema "
            f"({FORMAT_VIDEO} ou {FORMAT_CHANNEL})."
        )
    if int(data.get("version", 0)) > CURRENT_VERSION:
        raise InvalidState("Este arquivo foi criado por uma versão mais nova do sistema")
    try:
        return (
            VideoBundle.model_validate(data)
            if kind == FORMAT_VIDEO
            else ChannelBundle.model_validate(data)
        )
    except ValidationError as e:
        first = e.errors()[0]
        raise InvalidState(
            f"Arquivo inválido em {'.'.join(map(str, first['loc']))}: {first['msg']}"
        ) from e


def import_video(
    session: Session, channel_id: int, bundle: VideoBundle, actor: User, title: str | None = None
) -> Video:
    """Cria um vídeo novo (nunca sobrescreve) com todas as versões de roteiro do arquivo."""
    data = bundle.video
    video = videos.create_video(
        session,
        channel_id,
        VideoCreate(topic=data.topic, title=(title or data.title).strip(), language=data.language),
        actor,
        enqueue_script=False,
    )
    assert video.id is not None
    videos.update_video(
        session, video.id, VideoUpdate(description=data.description, tags=data.tags)
    )
    for script in sorted(bundle.scripts, key=lambda s: s.number):
        version = scripts.save_script(session, video.id, script.scenes, actor, author_kind="import")
        if script.approved:
            assert version.id is not None
            scripts.approve_script(session, version.id, actor)
    return videos.get_video(session, video.id)


def _unique_name(session: Session, base: str) -> str:
    existing = {c.slug for c in channels.list_channels(session, include_archived=True)}
    if channels.slugify(base) not in existing:
        return base
    n = 2
    while channels.slugify(f"{base} ({n})") in existing:
        n += 1
    return f"{base} ({n})"


def import_channel(
    session: Session,
    bundle: ChannelBundle,
    actor: User,
    name: str | None = None,
    include_videos: bool = True,
) -> Channel:
    """Cria um canal novo. Sem `name`, usa o do arquivo (com sufixo " (2)" se já existir)."""
    data = bundle.channel.model_copy()
    if name and name.strip():
        data.name = name.strip()
        if channels.slugify(data.name) in {
            c.slug for c in channels.list_channels(session, include_archived=True)
        }:
            raise Conflict(f"Já existe um canal com o nome '{data.name}'. Escolha outro nome.")
    else:
        data.name = _unique_name(session, data.name)
    channel = channels.create_channel(session, data)
    assert channel.id is not None
    if include_videos:
        for vb in bundle.videos:
            import_video(session, channel.id, vb, actor)
    return channel
