"""Render dos Shorts 9:16: reaproveita o construtor de segmentos do render longo com layout vertical."""

import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, Job, User, Video
from canaisdark.schemas.shorts import ShortSelection, ShortsPlan
from canaisdark.schemas.timeline import MotionKind, Timeline
from canaisdark.services import channels as channels_service
from canaisdark.services import (
    narration,
    render,
    scene_assets,
    shorts,
    storage,
    subtitles,
    videos,
)
from canaisdark.services import timeline as tl_service
from canaisdark.services.errors import InvalidState, NotFound

HOOK_SECONDS = 2.5
SHORT_TRANSITION_S = 0.4
MIN_CROSSFADE_S = 1.5
FADE_OUT_S = 0.3


def _timeline_for(session: Session, video: Video) -> Timeline:
    found = tl_service.latest(session, video.id or 0)
    return found[1] if found else tl_service.build_default(session, video)


def plan_short(
    session: Session,
    video: Video,
    sel: ShortSelection,
    index: int,
    quality: render.Quality,
    out_dir: Path,
    allow_missing: bool = False,
) -> render.RenderPlan:
    """Plano de render de UM Short (puro até gravar o arquivo de legendas)."""
    timing = tl_service.collect_timing(session, video)
    timeline = _timeline_for(session, video)
    t0, t1 = sel.start_s, sel.end_s
    spans = [sp for sp in timing.spans if sp.end > t0 + 0.05 and sp.start < t1 - 0.05]
    if not spans:
        raise InvalidState(f"Short {index + 1}: nenhuma cena cobre esse trecho da narração.")
    amap = scene_assets.scene_assets(session, [sp.scene.id for sp in spans if sp.scene.id])
    sources: list[dict[str, Any]] = []
    motions: list[dict[str, Any]] = []
    missing: list[int] = []
    for sp in spans:
        variants = amap.get(sp.scene.id or 0, {})
        asset = variants.get("vertical") or variants.get("main")
        if asset is None:
            missing.append(sp.scene.index + 1)
            sources.append(render._blank(sp.scene.index))
        else:
            src = render._source_for(asset, sp.scene.index)
            src["layout"] = "fill" if variants.get("vertical") is asset else "blur"
            sources.append(src)
        edit = timeline.for_scene(sp.scene.index)
        if edit:
            motions.append(edit.motion.model_dump(mode="json"))
        else:
            motions.append({"kind": MotionKind.ZOOM_IN.value, "intensity": 0.08})
    if missing and not allow_missing:
        shown = ", ".join(str(m) for m in missing[:12])
        raise InvalidState(
            f"Short {index + 1}: faltam imagens nas cenas {shown}. Envie-as no storyboard "
            "ou marque “preencher com fundo escuro”."
        )
    starts = [max(sp.start, t0) - t0 for sp in spans]
    starts[0] = 0.0
    total = t1 - t0
    lengths = [
        (starts[i + 1] if i + 1 < len(starts) else total) - starts[i] for i in range(len(starts))
    ]
    requested = [
        0.0
        if i == 0 or lengths[i] < MIN_CROSSFADE_S or lengths[i - 1] < MIN_CROSSFADE_S
        else SHORT_TRANSITION_S
        for i in range(len(starts))
    ]
    transitions = render.clamp_transitions(requested, lengths)
    segments = render.build_segments(
        sources,
        motions,
        starts,
        total,
        transitions,
        quality,
        0.0,
        FADE_OUT_S,
        timeline.effects.vignette,
        False,
    )
    words, _ = shorts.load_words(session, video)
    out_dir.mkdir(parents=True, exist_ok=True)
    ass = out_dir / f"short{index + 1}.ass"
    ass.write_text(subtitles.to_ass_shorts(words, t0, t1), encoding="utf-8")
    texts: list[tuple[float, float, str, str]] = []
    if sel.hook_override:
        texts.append((0.0, min(HOOK_SECONDS, total), sel.hook_override, "top"))
    return render.RenderPlan(
        quality=quality,
        segments=segments,
        total_frames=round(total * quality.fps),
        duration=total,
        audio=narration.asset_path(timing.audio),
        partial=False,
        subtitles_ass=ass,
        overlay_texts=texts,
        music=render.music_file(session, timeline),
        music_db=timeline.music.volume_db,
        ducking=timeline.music.ducking,
        warnings=[f"Cena(s) {missing} sem imagem: fundo escuro."] if missing else [],
        missing=missing,
        audio_start=t0,
        audio_fade=True,
    )


def shorts_dir(session: Session, video: Video) -> Path:
    channel = channels_service.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=True) / "shorts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_shorts(session: Session, video_id: int, kind: str | None = None) -> list[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.SHORT.value)
        .order_by(col(Asset.id).desc())
    )
    return [a for a in session.exec(stmt) if kind is None or a.meta.get("kind") == kind]


def best_for(session: Session, video_id: int) -> dict[int, Asset]:
    """{índice do Short: render mais recente, preferindo o final}."""
    out: dict[int, Asset] = {}
    for kind in ("preview", "final"):  # final por último sobrescreve
        for a in reversed(list_shorts(session, video_id, kind)):
            out[int(a.meta.get("index", -1))] = a
    return out


def _store(
    session: Session,
    video: Video,
    result: render.RenderResult,
    sel: ShortSelection,
    index: int,
    kind: str,
    plan_asset_id: int,
    q: render.Quality,
    job_id: int | None,
) -> Asset:
    assert video.id is not None
    for a in list_shorts(session, video.id, kind):
        if a.meta.get("index") == index:
            (get_settings().data_dir / a.path).unlink(missing_ok=True)
            session.delete(a)
    asset = Asset(
        video_id=video.id,
        type=AssetType.SHORT.value,
        path=narration.relpath(result.path),
        meta={
            "kind": kind,
            "index": index,
            "title": sel.title,
            "start_s": sel.start_s,
            "end_s": sel.end_s,
            "duration": round(result.duration, 2),
            "bytes": result.size,
            "width": q.width,
            "height": q.height,
            "plan_asset_id": plan_asset_id,
            "created_unix": int(time.time()),
        },
        based_on_script_version_id=video.approved_script_version_id,
        job_id=job_id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def start(
    session: Session,
    video_id: int,
    actor: User,
    quality: str = "preview",
    index: int | None = None,
    allow_missing: bool = False,
) -> Job:
    """Valida (erros claros) e enfileira o job `shorts` (todos os Shorts ou só `index`, 0-based)."""
    from canaisdark.models import JobType
    from canaisdark.services import jobs

    render.ffmpeg_ok()
    if quality not in render.SHORT_QUALITIES:
        raise InvalidState("Qualidade inválida. Use: preview ou final.")
    video = videos.get_video(session, video_id)
    found = shorts.latest_plan(session, video_id)
    if found is None or not found[1].shorts:
        raise InvalidState("Ainda não há Shorts selecionados. Escolha os trechos primeiro.")
    asset, plan = found
    if plan.skipped:
        raise InvalidState(
            "Os Shorts deste vídeo estão marcados como dispensados. Reative-os antes."
        )
    chosen = _chosen(plan, index)
    out_dir = shorts_dir(session, video)
    q = render.SHORT_QUALITIES[quality]
    for i, sel in chosen:  # só valida
        plan_short(session, video, sel, i, q, out_dir / f"s{i + 1}", allow_missing)
    return jobs.create_job(
        session,
        video_id,
        JobType.SHORTS,
        {
            "quality": quality,
            "index": index,
            "allow_missing": allow_missing,
            "plan_asset_id": asset.id,
        },
        actor,
    )


def _chosen(plan: ShortsPlan, index: int | None) -> list[tuple[int, ShortSelection]]:
    items = list(enumerate(plan.shorts))
    if index is None:
        return items
    if not 0 <= index < len(items):
        raise NotFound(f"Short {index + 1} não existe (há {len(items)}).")
    return [items[index]]


def run_job(session: Session, job: Job) -> None:
    from canaisdark.services import jobs

    assert job.id is not None
    p = job.params
    video = videos.get_video(session, job.video_id)
    plan_asset = session.get(Asset, p["plan_asset_id"])
    if plan_asset is None:
        raise InvalidState("O plano de Shorts deste render não existe mais.")
    plan = shorts.load_plan(plan_asset)
    q = render.SHORT_QUALITIES[p["quality"]]
    chosen = _chosen(plan, p.get("index"))
    out_root = shorts_dir(session, video)
    started = time.monotonic()
    ids: list[int] = []
    total = len(chosen)
    for n, (i, sel) in enumerate(chosen):
        folder = out_root / f"s{i + 1}"
        rplan = plan_short(session, video, sel, i, q, folder, bool(p.get("allow_missing")))
        jobs.append_log(
            session,
            job.id,
            f"Short {i + 1}/{len(plan.shorts)} “{sel.title}”: {rplan.duration:.0f}s, "
            f"{len(rplan.segments)} cenas, {q.width}x{q.height}.",
        )

        def progress(done: int, tot: int, message: str, n: int = n) -> None:
            pct = int((n + done / max(tot, 1)) * 100 / total)
            jobs.heartbeat(session, job.id or 0, min(pct, 99))
            if message:
                jobs.append_log(session, job.id or 0, message)

        result = render.execute(
            rplan,
            folder,
            f"{q.name}-{int(time.time())}.mp4",
            on_progress=progress,
            is_cancelled=lambda: jobs.is_cancelled(session, job.id or 0),
        )
        out = _store(session, video, result, sel, i, q.name, plan_asset.id or 0, q, job.id)
        ids.append(out.id or 0)
        for old in folder.glob("*.mp4"):
            if old.name != result.path.name and not old.name.startswith("segments"):
                old.unlink(missing_ok=True)
    elapsed = time.monotonic() - started
    jobs.append_log(session, job.id, f"Shorts prontos em {elapsed:.0f}s ({len(ids)} arquivo(s)).")
    jobs.complete(session, job.id, {"asset_ids": ids, "seconds": round(elapsed, 1)})
