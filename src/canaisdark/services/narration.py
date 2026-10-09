"""Planejamento da narração: trechos, estimativa de créditos, proteções e criação do job."""

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import Subscription, credit_multiplier
from canaisdark.models import Asset, AssetType, Job, JobType, Scene, ScriptVersion, User, Video
from canaisdark.services import channels, jobs, scripts, storage, videos
from canaisdark.services.errors import InvalidState
from canaisdark.services.script_text import split_sentences


@dataclass
class Chunk:
    text: str
    scenes: list[int] = field(default_factory=list)


def build_chunks(scene_texts: list[str], max_chars: int) -> list[Chunk]:
    """Agrupa cenas inteiras até `max_chars`; cena maior que o limite é quebrada por frases."""
    chunks: list[Chunk] = []
    current = Chunk("")
    for i, text in enumerate(scene_texts):
        pieces = [text] if len(text) <= max_chars else _split_long(text, max_chars)
        for piece in pieces:
            if current.text and len(current.text) + 1 + len(piece) > max_chars:
                chunks.append(current)
                current = Chunk("")
            current.text = f"{current.text} {piece}".strip()
            if i not in current.scenes:
                current.scenes.append(i)
    if current.text:
        chunks.append(current)
    return chunks


def _split_long(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    buf = ""
    for sentence in split_sentences(text):
        if len(sentence) > max_chars:  # frase gigante: corta em palavras
            words, line = sentence.split(), ""
            for w in words:
                if line and len(line) + 1 + len(w) > max_chars:
                    out.append(line)
                    line = ""
                line = f"{line} {w}".strip()
            sentence = line
        if buf and len(buf) + 1 + len(sentence) > max_chars:
            out.append(buf)
            buf = ""
        buf = f"{buf} {sentence}".strip()
    if buf:
        out.append(buf)
    return out


def chunk_key(
    text: str, voice_id: str, model_id: str, stability: float, similarity: float, speed: float
) -> str:
    """Identifica um trecho já gerado (permite retomar sem pagar de novo)."""
    raw = "|".join([text, voice_id, model_id, str(stability), str(similarity), str(speed)])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class NarrationPlan:
    video_id: int
    script_version: ScriptVersion | None
    scene_texts: list[str]
    scenes_total: int
    chunks: list[Chunk]
    chars: int
    credits_estimated: int
    model_id: str
    voice_id: str
    partial: bool
    will_align: bool
    max_chars: int
    balance: Subscription | None
    api_key_set: bool
    problems: list[str]

    @property
    def can_start(self) -> bool:
        return not self.problems

    @property
    def full_text(self) -> str:
        return " ".join(self.scene_texts)


def plan(
    session: Session,
    video_id: int,
    test_scenes: int | None = None,
    align: bool | None = None,
    balance: Subscription | None = None,
) -> NarrationPlan:
    settings = get_settings()
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    problems: list[str] = []
    version: ScriptVersion | None = None
    texts: list[str] = []
    total = 0
    if video.approved_script_version_id is None:
        problems.append("O roteiro ainda não foi aprovado. Aprove antes de gastar créditos.")
    else:
        version = scripts.get_version(session, video.approved_script_version_id)
        all_scenes = scripts.scenes_of(session, version)
        total = len(all_scenes)
        used = all_scenes[:test_scenes] if test_scenes else all_scenes
        texts = [s.text for s in used]
    if not channel.elevenlabs_voice_id.strip():
        problems.append("Defina o voice_id do ElevenLabs nas configurações do canal.")
    if not settings.elevenlabs_ready:
        problems.append("ELEVENLABS_API_KEY não está configurada no .env do servidor.")
    max_chars = settings.elevenlabs_max_chars_per_job
    chars = sum(len(t) for t in texts) + max(0, len(texts) - 1)
    if chars > max_chars:
        problems.append(
            f"São {chars} caracteres e o limite por geração é {max_chars} (ELEVENLABS_MAX_CHARS_PER_JOB). "
            "Use o teste curto (primeiras cenas) ou aumente o limite no .env."
        )
    credits = math.ceil(chars * credit_multiplier(channel.tts_model))
    if balance is not None and credits > balance.remaining:
        problems.append(f"Créditos insuficientes: estimado {credits}, saldo {balance.remaining}.")
    will_align = settings.forced_alignment_enabled and (
        channel.forced_alignment if align is None else align
    )
    return NarrationPlan(
        video_id=video_id,
        script_version=version,
        scene_texts=texts,
        scenes_total=total,
        chunks=build_chunks(texts, settings.tts_chunk_chars) if texts else [],
        chars=chars,
        credits_estimated=credits,
        model_id=channel.tts_model,
        voice_id=channel.elevenlabs_voice_id.strip(),
        partial=bool(test_scenes) and len(texts) < total,
        will_align=will_align,
        max_chars=max_chars,
        balance=balance,
        api_key_set=settings.elevenlabs_ready,
        problems=problems,
    )


def start(
    session: Session,
    video_id: int,
    actor: User,
    test_scenes: int | None = None,
    align: bool | None = None,
    balance: Subscription | None = None,
) -> Job:
    p = plan(session, video_id, test_scenes, align, balance)
    if not p.can_start:
        raise InvalidState(" ".join(p.problems))
    assert p.script_version is not None
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    params = {
        "script_version_id": p.script_version.id,
        "scene_count": len(p.scene_texts),
        "partial": p.partial,
        "align": p.will_align,
        "voice_id": p.voice_id,
        "model_id": p.model_id,
        "stability": channel.tts_stability,
        "similarity": channel.tts_similarity,
        "speed": channel.tts_speed,
        "max_chars": p.max_chars,
        "chunk_chars": get_settings().tts_chunk_chars,
        "chars": p.chars,
        "credits_estimated": p.credits_estimated,
    }
    return jobs.create_job(session, video_id, JobType.TTS, params, actor)


# ----- Arquivos / ativos -----


def audio_dir(session: Session, video: Video, create: bool = True) -> Path:
    channel = channels.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=create) / "audio"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def relpath(path: Path) -> str:
    return str(path.relative_to(get_settings().data_dir))


def asset_path(asset: Asset) -> Path:
    return get_settings().data_dir / asset.path


def latest_asset(
    session: Session, video_id: int, asset_type: AssetType, kind: str | None = None
) -> Asset | None:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == asset_type.value)
        .order_by(col(Asset.id).desc())
    )
    for asset in session.exec(stmt):
        if kind is None or asset.meta.get("kind") == kind:
            return asset
    return None


def scenes_for_version(session: Session, version_id: int, count: int) -> list[Scene]:
    return scripts.scenes_of(session, scripts.get_version(session, version_id))[:count]


def latest_subtitle(session: Session, video_id: int, fmt: str) -> Asset | None:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.SUBTITLE.value)
        .order_by(col(Asset.id).desc())
    )
    return next((a for a in session.exec(stmt) if a.meta.get("format") == fmt), None)
