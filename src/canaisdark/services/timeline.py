"""Timeline de edição: tempos das cenas, timeline padrão automática, validação e versões salvas."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, Scene, Video
from canaisdark.schemas.timeline import (
    Motion,
    MotionKind,
    SceneEdit,
    Timeline,
    Transition,
    TransitionKind,
)
from canaisdark.services import channels, narration, scene_assets, scripts, storage, videos
from canaisdark.services.errors import InvalidState

TAIL_S = 0.8  # imagem final continua um pouco depois da última palavra (fade-out)
MIN_CROSSFADE_SCENE_S = 1.5
SHORT_SCENE_S = 3.0
# Sequência sem repetir o mesmo movimento em cenas vizinhas.
MOTION_CYCLE = (
    MotionKind.ZOOM_IN,
    MotionKind.PAN_LEFT,
    MotionKind.ZOOM_OUT,
    MotionKind.PAN_RIGHT,
    MotionKind.ZOOM_IN,
    MotionKind.PAN_UP,
    MotionKind.ZOOM_OUT,
    MotionKind.PAN_DOWN,
)


@dataclass
class SceneSpan:
    """Intervalo em que a cena fica na tela (de seu início até o início da seguinte)."""

    scene: Scene
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class Timing:
    spans: list[SceneSpan]
    audio: Asset
    duration: float  # duração total do vídeo (áudio + cauda)
    partial: bool


def approved_scenes(session: Session, video: Video) -> list[Scene]:
    if video.approved_script_version_id is None:
        raise InvalidState("O roteiro ainda não foi aprovado.")
    return scripts.scenes_of(
        session, scripts.get_version(session, video.approved_script_version_id)
    )


def collect_timing(session: Session, video: Video) -> Timing:
    """Cenas com tempo + áudio da narração. Em teste curto, só as cenas já narradas."""
    assert video.id is not None
    scenes = approved_scenes(session, video)
    audio = narration.latest_asset(session, video.id, AssetType.AUDIO, "narration")
    if audio is None:
        raise InvalidState("A narração ainda não foi gerada.")
    timed = [s for s in scenes if s.start_s is not None and s.end_s is not None]
    if not timed:
        raise InvalidState("As cenas ainda não têm tempos (a narração precisa estar pronta).")
    audio_duration = float(audio.meta.get("duration") or timed[-1].end_s or 0.0)
    spans: list[SceneSpan] = []
    for i, scene in enumerate(timed):
        start = 0.0 if i == 0 else float(scene.start_s or 0.0)
        end = (
            float(timed[i + 1].start_s or 0.0)
            if i + 1 < len(timed)
            else max(audio_duration, float(scene.end_s or 0.0)) + TAIL_S
        )
        spans.append(SceneSpan(scene, start, max(end, start + 0.1)))
    return Timing(
        spans=spans,
        audio=audio,
        duration=spans[-1].end,
        partial=len(timed) < len(scenes) or bool(audio.meta.get("partial")),
    )


def main_assets(session: Session, scenes: list[Scene]) -> dict[int, Asset]:
    amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
    return {sid: v["main"] for sid, v in amap.items() if "main" in v}


def inputs_hash(session: Session, video: Video) -> str:
    """Muda quando o áudio, os tempos ou as imagens das cenas mudam (timeline/render ficam desatualizados)."""
    timing = collect_timing(session, video)
    assets = main_assets(session, [sp.scene for sp in timing.spans])
    payload = {
        "audio": timing.audio.id,
        "scenes": [
            [sp.scene.index, round(sp.start, 2), round(sp.end, 2), assets[sp.scene.id].id]
            if sp.scene.id in assets
            else [sp.scene.index, round(sp.start, 2), round(sp.end, 2), None]
            for sp in timing.spans
        ],
    }
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


# ----- Timeline padrão -----


def default_transition(prev: SceneSpan, cur: SceneSpan) -> Transition:
    shortest = min(prev.length, cur.length)
    if shortest < MIN_CROSSFADE_SCENE_S:
        return Transition(kind=TransitionKind.CUT, duration=0.0)
    return Transition(
        kind=TransitionKind.CROSSFADE, duration=0.3 if shortest < SHORT_SCENE_S else 0.6
    )


def build_default(session: Session, video: Video, base: Timeline | None = None) -> Timeline:
    """Timeline automática. Com `base`, preserva as escolhas já feitas nas cenas que continuam existindo."""
    assert video.id is not None
    timing = collect_timing(session, video)
    assets = main_assets(session, [sp.scene for sp in timing.spans])
    edits: list[SceneEdit] = []
    for i, sp in enumerate(timing.spans):
        previous = base.for_scene(sp.scene.index) if base else None
        if previous is not None:
            edits.append(previous)
            continue
        asset = assets.get(sp.scene.id or 0)
        is_clip = asset is not None and asset.type == AssetType.VIDEO_CLIP.value
        kind = MotionKind.STATIC if is_clip else MOTION_CYCLE[(i + video.id) % len(MOTION_CYCLE)]
        transition = (
            default_transition(timing.spans[i - 1], sp)
            if i
            else Transition(kind=TransitionKind.CUT)
        )
        edits.append(
            SceneEdit(
                scene_index=sp.scene.index,
                motion=Motion(kind=kind, intensity=0.08 if kind.startswith("pan") else 0.10),
                transition_in=transition,
            )
        )
    if base is None:
        return Timeline(scenes=edits)
    return base.model_copy(update={"scenes": edits})


# ----- Validação -----


def validate_for_video(session: Session, video: Video, timeline: Timeline) -> list[str]:
    """Erros em português; lista vazia = válida. Avisos de cenas sem imagem não são erros aqui."""
    assert video.id is not None
    scenes = approved_scenes(session, video)
    errors: list[str] = []
    valid = {s.index for s in scenes}
    for edit in timeline.scenes:
        if edit.scene_index not in valid:
            errors.append(
                f"scene_index {edit.scene_index} não existe: o roteiro aprovado tem {len(scenes)} "
                f"cenas (índices 0 a {len(scenes) - 1})."
            )
    own = {
        a.id: a
        for a in session.exec(
            select(Asset).where(
                Asset.video_id == video.id,
                col(Asset.type).in_(
                    [AssetType.IMAGE.value, AssetType.VIDEO_CLIP.value, AssetType.MUSIC.value]
                ),
            )
        )
    }
    for edit in timeline.scenes:
        if edit.asset_id is not None:
            asset = own.get(edit.asset_id)
            if asset is None or asset.type == AssetType.MUSIC.value:
                errors.append(
                    f"Cena {edit.scene_index}: asset_id {edit.asset_id} não é uma imagem/clipe deste vídeo."
                )
    if timeline.music.asset_id is not None:
        music = own.get(timeline.music.asset_id)
        if music is None or music.type != AssetType.MUSIC.value:
            errors.append(f"music.asset_id {timeline.music.asset_id} não é uma trilha deste vídeo.")
    return errors


def ensure_valid(session: Session, video: Video, timeline: Timeline) -> None:
    errors = validate_for_video(session, video, timeline)
    if errors:
        raise InvalidState(" ".join(errors))


# ----- Versões salvas -----


def _folder(session: Session, video: Video) -> Path:
    channel = channels.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=True) / "timeline"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_versions(session: Session, video_id: int) -> list[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.TIMELINE.value)
        .order_by(col(Asset.id))
    )
    return list(session.exec(stmt))


def save(session: Session, video_id: int, timeline: Timeline, source: str, author: str) -> Asset:
    """Salva SEMPRE uma versão nova (nunca sobrescreve). `source`: auto | user | claude."""
    video = videos.get_video(session, video_id)
    ensure_valid(session, video, timeline)
    number = len(list_versions(session, video_id)) + 1
    path = _folder(session, video) / f"timeline_v{number}.json"
    path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
    asset = Asset(
        video_id=video_id,
        type=AssetType.TIMELINE.value,
        path=narration.relpath(path),
        meta={
            "source": source,
            "author": author,
            "number": number,
            "inputs_hash": inputs_hash(session, video),
        },
        based_on_script_version_id=video.approved_script_version_id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def load(asset: Asset) -> Timeline:
    return Timeline.model_validate_json(
        (get_settings().data_dir / asset.path).read_text(encoding="utf-8")
    )


def latest(session: Session, video_id: int) -> tuple[Asset, Timeline] | None:
    versions = list_versions(session, video_id)
    if not versions:
        return None
    asset = versions[-1]
    return asset, load(asset)


def ensure_timeline(session: Session, video_id: int, author: str) -> tuple[Asset, Timeline]:
    """A última timeline salva ou, se não houver, a padrão (salva como `auto`)."""
    found = latest(session, video_id)
    if found:
        return found
    video = videos.get_video(session, video_id)
    asset = save(session, video_id, build_default(session, video), "auto", author)
    return asset, load(asset)
