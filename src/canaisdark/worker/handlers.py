"""Handlers dos jobs de narração. Cada handler recebe a Session, o Job e uma fábrica de cliente."""

import json
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from canaisdark.integrations.elevenlabs import ElevenLabsLike, GenerationCancelled
from canaisdark.models import Asset, AssetType, Job, JobType, Scene
from canaisdark.models.channel import utcnow
from canaisdark.services import alignment, audio, jobs, narration, scripts, subtitles, videos
from canaisdark.services.errors import InvalidState

ClientFactory = Callable[[], ElevenLabsLike]


class JobCancelled(Exception):
    pass


def _check_cancel(session: Session, job: Job) -> None:
    assert job.id is not None
    if jobs.is_cancelled(session, job.id):
        raise JobCancelled


def _credits(client: ElevenLabsLike) -> int | None:
    sub = client.subscription()
    return sub.used if sub else None


def store_timing_outputs(
    session: Session,
    job: Job,
    scenes: list[Scene],
    spans: list[tuple[float, float]],
    words: list[dict[str, Any]],
    timing: str,
    duration: float,
    audio_asset: Asset,
    loss: float | None = None,
) -> dict[str, Any]:
    """Grava tempos nas cenas e os arquivos alignment.json/.srt/.ass como ativos."""
    video = videos.get_video(session, job.video_id)
    texts = [s.text for s in scenes]
    for scene, (start, end) in zip(scenes, spans, strict=True):
        scene.start_s, scene.end_s = round(start, 3), round(end, 3)
        session.add(scene)
    # cenas fora do teste curto ficam sem tempo
    for scene in scripts.scenes_of(
        session,
        scripts.get_version(session, int(audio_asset.based_on_script_version_id or 0)),
    ):
        if scene.id not in {s.id for s in scenes} and (
            scene.start_s is not None or scene.end_s is not None
        ):
            scene.start_s = scene.end_s = None
            session.add(scene)
    doc = alignment.build_alignment_doc(timing, duration, words, texts, spans, loss)
    folder = narration.audio_dir(session, video)
    stem = f"{audio_asset.path.rsplit('/', 1)[-1].removesuffix('.mp3')}"
    meta = {"timing": timing, "audio_asset_id": audio_asset.id}
    files = {
        "alignment": (
            folder / f"{stem}.alignment.json",
            json.dumps(doc, ensure_ascii=False, indent=2),
            AssetType.ALIGNMENT,
            {"kind": "alignment"},
        ),
        "srt": (
            folder / f"{stem}.srt",
            subtitles.to_srt(words),
            AssetType.SUBTITLE,
            {"kind": "subtitle", "format": "srt"},
        ),
        "ass": (
            folder / f"{stem}.ass",
            subtitles.to_ass(words),
            AssetType.SUBTITLE,
            {"kind": "subtitle", "format": "ass"},
        ),
    }
    for path, content, atype, extra in files.values():
        path.write_text(content, encoding="utf-8")
        session.add(
            Asset(
                video_id=job.video_id,
                type=atype.value,
                path=narration.relpath(path),
                meta={**meta, **extra},
                based_on_script_version_id=audio_asset.based_on_script_version_id,
                job_id=job.id,
            )
        )
    session.commit()
    return doc


def run_tts(session: Session, job: Job, make_client: ClientFactory) -> None:
    assert job.id is not None
    job_id = job.id
    p = job.params
    video = videos.get_video(session, job.video_id)
    scenes = narration.scenes_for_version(session, p["script_version_id"], p["scene_count"])
    texts = [s.text for s in scenes]
    chunks = narration.build_chunks(texts, p.get("chunk_chars", 2500))
    jobs.append_log(
        session,
        job.id,
        f"Narração: {len(chunks)} trecho(s), {p['chars']} caracteres, modelo {p['model_id']}"
        f"{' (TESTE CURTO)' if p.get('partial') else ''}.",
    )
    client = make_client()
    before = _credits(client)
    if before is not None:
        jobs.append_log(session, job.id, f"Créditos usados no mês antes: {before}.")
    parts_dir = narration.audio_dir(session, video) / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, chunk in enumerate(chunks):
        _check_cancel(session, job)
        key = narration.chunk_key(
            chunk.text, p["voice_id"], p["model_id"], p["stability"], p["similarity"], p["speed"]
        )
        part = parts_dir / f"{key}.mp3"
        if part.exists():
            jobs.append_log(
                session,
                job.id,
                f"Trecho {i + 1}/{len(chunks)}: reaproveitado (já gerado antes, sem custo).",
            )
        else:
            try:
                data = client.text_to_speech(
                    p["voice_id"],
                    chunk.text,
                    model_id=p["model_id"],
                    stability=p["stability"],
                    similarity=p["similarity"],
                    speed=p["speed"],
                    previous_text=chunks[i - 1].text[-250:] if i > 0 else "",
                    next_text=chunks[i + 1].text[:250] if i + 1 < len(chunks) else "",
                    should_cancel=lambda: jobs.is_cancelled(session, job_id),
                )
            except GenerationCancelled:
                jobs.append_log(
                    session,
                    job_id,
                    f"Cancelado durante o trecho {i + 1}: a espera foi interrompida (esse trecho pode já ter sido cobrado).",
                )
                raise JobCancelled from None
            part.write_bytes(data)
            jobs.append_log(
                session,
                job.id,
                f"Trecho {i + 1}/{len(chunks)}: gerado ({len(chunk.text)} caracteres).",
            )
        parts.append(part)
        jobs.heartbeat(session, job.id, int(5 + 70 * (i + 1) / len(chunks)))
    _check_cancel(session, job)
    version = scripts.get_version(session, p["script_version_id"])
    out = narration.audio_dir(session, video) / f"narration_v{version.number}_{job.id}.mp3"
    audio.concat_mp3(parts, out)
    duration = audio.duration_seconds(out)
    asset = Asset(
        video_id=job.video_id,
        type=AssetType.AUDIO.value,
        path=narration.relpath(out),
        meta={
            "kind": "narration",
            "duration": round(duration, 2),
            "chars": p["chars"],
            "partial": bool(p.get("partial")),
            "model_id": p["model_id"],
            "voice_id": p["voice_id"],
            "parts": len(parts),
            "align": bool(p["align"]),
        },
        based_on_script_version_id=p["script_version_id"],
        job_id=job.id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    after = _credits(client)
    used = after - before if before is not None and after is not None else None
    if used:
        jobs.append_log(session, job.id, f"Créditos gastos neste TTS: {used}.")
    else:
        jobs.append_log(
            session,
            job.id,
            f"Créditos estimados neste TTS: {p.get('credits_estimated')} (o contador do ElevenLabs atualiza com atraso; veja o saldo no card).",
        )
    jobs.append_log(session, job.id, f"Áudio pronto: {duration:.1f}s.")
    result: dict[str, Any] = {
        "audio_asset_id": asset.id,
        "duration": round(duration, 2),
        "credits_used": used,
        "align": bool(p["align"]),
    }
    if p["align"]:
        jobs.create_job(
            session,
            job.video_id,
            JobType.ALIGNMENT,
            {
                "audio_asset_id": asset.id,
                "script_version_id": p["script_version_id"],
                "scene_count": p["scene_count"],
            },
            parent_job_id=job.id,
        )
        jobs.append_log(session, job.id, "Alinhamento enfileirado.")
    else:
        spans = alignment.estimate_scene_times(texts, duration)
        words = alignment.words_from_scene_spans(texts, spans)
        store_timing_outputs(session, job, scenes, spans, words, "estimated", duration, asset)
        jobs.append_log(
            session, job.id, "Forced Alignment desligado: tempos das cenas e legendas ESTIMADOS."
        )
    jobs.complete(session, job.id, result)


def run_alignment(session: Session, job: Job, make_client: ClientFactory) -> None:
    assert job.id is not None
    p = job.params
    asset = session.get(Asset, p["audio_asset_id"])
    if asset is None:
        raise InvalidState("O áudio deste alinhamento não existe mais.")
    scenes = narration.scenes_for_version(session, p["script_version_id"], p["scene_count"])
    texts = [s.text for s in scenes]
    full_text = " ".join(texts)
    audio_bytes = narration.asset_path(asset).read_bytes()
    client = make_client()
    before = _credits(client)
    jobs.append_log(
        session,
        job.id,
        f"Forced Alignment de {len(audio_bytes) / 1024:.0f} KB e {len(full_text)} caracteres.",
    )
    jobs.heartbeat(session, job.id, 20)
    data = client.forced_alignment(audio_bytes, full_text)
    _check_cancel(session, job)
    words = alignment.clean_words(data.get("words") or [])
    if not words:
        raise InvalidState("O ElevenLabs não devolveu palavras alinhadas.")
    spans = [s or (0.0, 0.0) for s in alignment.map_words_to_scenes(words, texts)]
    duration = float(asset.meta.get("duration") or words[-1]["end"])
    doc = store_timing_outputs(
        session, job, scenes, spans, words, "forced", duration, asset, data.get("loss")
    )
    after = _credits(client)
    used = after - before if before is not None and after is not None else None
    if used:
        jobs.append_log(session, job.id, f"Créditos gastos no alinhamento: {used}.")
    else:
        jobs.append_log(
            session,
            job.id,
            "Custo do alinhamento: o contador do ElevenLabs ainda não refletiu (compare o saldo do card antes/depois).",
        )
    jobs.append_log(
        session,
        job.id,
        f"Alinhamento concluído: {len(words)} palavras, {len(doc['low_confidence'])} suspeitas.",
    )
    jobs.complete(
        session,
        job.id,
        {
            "words": len(words),
            "loss": data.get("loss"),
            "credits_used": used,
            "low_confidence": len(doc["low_confidence"]),
            "finished": utcnow().isoformat(),
        },
    )


HANDLERS: dict[str, Callable[[Session, Job, ClientFactory], None]] = {
    JobType.TTS.value: run_tts,
    JobType.ALIGNMENT.value: run_alignment,
}
