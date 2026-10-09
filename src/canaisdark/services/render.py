"""Render do vídeo com ffmpeg.

Cada cena vira um SEGMENTO MP4 independente (movimento Ken Burns por `zoompan`). O final do segmento já contém o
crossfade para a cena seguinte (xfade entre as duas imagens) e a cena seguinte começa de onde o crossfade terminou;
assim a concatenação (`-c copy`) é contínua e cada segmento pode ser cacheado/retomado/re-renderizado sozinho.
Um passo final junta os segmentos, mixa narração + trilha (ducking) e queima legendas/textos se pedido.

As funções `plan`, `segment_command` e `final_command` são puras (não executam ffmpeg) e por isso testáveis.
"""

import hashlib
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, Job, User, Video
from canaisdark.schemas.timeline import MotionKind, Timeline
from canaisdark.services import channels, narration, storage, videos
from canaisdark.services import timeline as tl_service
from canaisdark.services.errors import InvalidState

ALGO_VERSION = 2  # muda quando a forma de montar os segmentos muda (invalida o cache)
BLANK_COLOR = "0x0b0d12"
SUPERSAMPLE = 2


@dataclass(frozen=True)
class Quality:
    name: str
    width: int
    height: int
    fps: int
    preset: str
    crf: int


PREVIEW = Quality("preview", 1280, 720, 24, "veryfast", 26)
FINAL = Quality("final", 1920, 1080, 30, "fast", 18)
QUALITIES = {q.name: q for q in (PREVIEW, FINAL)}
# Shorts 9:16 (reusam o mesmo construtor de segmentos)
SHORT_PREVIEW = Quality("preview", 720, 1280, 24, "veryfast", 26)
SHORT_FINAL = Quality("final", 1080, 1920, 30, "fast", 18)
SHORT_QUALITIES = {q.name: q for q in (SHORT_PREVIEW, SHORT_FINAL)}
BLUR_BAND_RATIO = 9 / 16  # a imagem 16:9 ocupa a largura toda do quadro vertical


class RenderCancelled(Exception):
    pass


@dataclass
class SegmentSpec:
    """Tudo o que define UM segmento (só dados simples: vira o hash do cache)."""

    position: int
    scene_index: int
    source: dict[str, Any]  # {"kind": image|clip|blank, "path": str, "stamp": [mtime_ns, size]}
    motion: dict[str, Any]  # {"kind", "intensity"}
    frames: int  # quadros do segmento inteiro
    scene_frames: int  # quadros totais em que a cena está visível (progresso do movimento)
    scene_offset: int  # quadro do relógio da cena em que o segmento começa
    blend_frames: int  # crossfade no final (0 = sem)
    next_source: dict[str, Any] | None = None
    next_motion: dict[str, Any] | None = None
    next_scene_frames: int = 0
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    vignette: bool = False
    grain: bool = False


@dataclass
class RenderPlan:
    quality: Quality
    segments: list[SegmentSpec]
    total_frames: int
    duration: float
    audio: Path
    partial: bool
    subtitles_ass: Path | None
    overlay_texts: list[tuple[float, float, str, str]]  # (início, fim, texto, posição)
    music: Path | None
    music_db: float
    ducking: bool
    warnings: list[str] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    audio_start: float = 0.0  # início (s) da fatia usada da narração (Shorts)
    audio_fade: bool = False  # fade curto de entrada/saída na narração (Shorts)


def ffmpeg_ok() -> None:
    if shutil.which("ffmpeg") is None:
        raise InvalidState("O ffmpeg não está instalado. Instale com: sudo apt install ffmpeg")


# ----- Planejamento (puro, a partir de dados) -----


def clamp_transitions(requested: list[float], lengths: list[float]) -> list[float]:
    """Duração efetiva do crossfade que ENTRA em cada cena (índice 0 sempre 0).

    A metade da transição fica de cada lado da fronteira, então as duas metades dentro de uma mesma cena
    somadas não podem passar de 90% dela."""
    out = [0.0] * len(lengths)
    for i in range(1, len(lengths)):
        limit = min(1.8 * lengths[i - 1] - out[i - 1], 1.8 * lengths[i])
        t = min(requested[i], limit)
        out[i] = t if t >= 0.1 else 0.0
    return out


def frame_layout(
    starts: list[float], total: float, transitions: list[float], fps: int
) -> tuple[list[tuple[int, int]], list[int], int]:
    """([(início, fim) do blend que ENTRA em cada cena], [início do relógio de cada cena], total de quadros)."""
    total_frames = round(total * fps)
    blends: list[tuple[int, int]] = [(0, 0)]
    for i in range(1, len(starts)):
        nb = round(transitions[i] * fps)
        fs = round((starts[i] - transitions[i] / 2) * fps) if nb else round(starts[i] * fps)
        blends.append((fs, fs + nb))
    clocks = [0] + [b[0] for b in blends[1:]]
    return blends, clocks, total_frames


def build_segments(
    sources: list[dict[str, Any]],
    motions: list[dict[str, Any]],
    starts: list[float],
    total: float,
    transitions: list[float],
    quality: Quality,
    fade_in_s: float,
    fade_out_s: float,
    vignette: bool,
    grain: bool,
) -> list[SegmentSpec]:
    fps = quality.fps
    n = len(sources)
    blends, clocks, total_frames = frame_layout(starts, total, transitions, fps)
    segments: list[SegmentSpec] = []
    for i in range(n):
        seg_start = 0 if i == 0 else blends[i][1]
        has_next = i + 1 < n
        seg_end = blends[i + 1][1] if has_next else total_frames
        blend = blends[i + 1][1] - blends[i + 1][0] if has_next else 0
        scene_end = blends[i + 1][1] if has_next else total_frames
        segments.append(
            SegmentSpec(
                position=i,
                scene_index=int(sources[i].get("scene_index", i)),
                source=sources[i],
                motion=motions[i],
                frames=max(1, seg_end - seg_start),
                scene_frames=max(1, scene_end - clocks[i]),
                scene_offset=max(0, seg_start - clocks[i]),
                blend_frames=blend,
                next_source=sources[i + 1] if has_next else None,
                next_motion=motions[i + 1] if has_next else None,
                next_scene_frames=max(
                    1, ((blends[i + 2][1] if i + 2 < n else total_frames) - clocks[i + 1])
                )
                if has_next
                else 0,
                fade_in_frames=round(fade_in_s * fps) if i == 0 else 0,
                fade_out_frames=round(fade_out_s * fps) if i == n - 1 else 0,
                vignette=vignette,
                grain=grain,
            )
        )
    return segments


# ----- Comandos ffmpeg (puros) -----


def _camera(kind: str, intensity: float, frames: int, offset: int) -> tuple[str, str, str]:
    """Expressões (zoom, x, y) do zoompan em função do quadro de saída `on` (0 = primeiro do segmento)."""
    p = f"(on+{offset})/{max(frames, 1)}"
    cx, cy = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    i = intensity
    if kind == MotionKind.ZOOM_IN:
        return f"1+{i}*{p}", cx, cy
    if kind == MotionKind.ZOOM_OUT:
        return f"1+{i}*(1-{p})", cx, cy
    if kind == MotionKind.PAN_LEFT:
        return f"1+{i}", f"(iw-iw/zoom)*(1-{p})", cy
    if kind == MotionKind.PAN_RIGHT:
        return f"1+{i}", f"(iw-iw/zoom)*{p}", cy
    if kind == MotionKind.PAN_UP:
        return f"1+{i}", cx, f"(ih-ih/zoom)*(1-{p})"
    if kind == MotionKind.PAN_DOWN:
        return f"1+{i}", cx, f"(ih-ih/zoom)*{p}"
    return "1", cx, cy


def _blur_chain(
    src: dict[str, Any],
    motion: dict[str, Any],
    scene_frames: int,
    offset: int,
    q: Quality,
    label_in: str,
    label_out: str,
) -> str:
    """Quadro vertical com a imagem 16:9 centralizada em largura total sobre o próprio fundo desfocado."""
    w, h = q.width, q.height
    band = round(w * BLUR_BAND_RATIO / 2) * 2
    o = label_out
    bg = (
        f"[{label_in}]split[{o}s1][{o}s2];"
        f"[{o}s1]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"boxblur=40:4,eq=brightness=-0.18,setsar=1[{o}bg];"
    )
    animated = src["kind"] != "clip" and motion["kind"] != MotionKind.STATIC
    if animated:
        z, x, y = _camera(motion["kind"], float(motion["intensity"]), scene_frames, offset)
        bw, bh = w * SUPERSAMPLE, band * SUPERSAMPLE
        fg = (
            f"[{o}s2]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{band}:fps={q.fps},setsar=1[{o}fg];"
        )
    else:
        fg = (
            f"[{o}s2]scale={w}:{band}:force_original_aspect_ratio=increase,crop={w}:{band},"
            f"fps={q.fps},setsar=1[{o}fg];"
        )
    return (
        f"{bg}{fg}[{o}bg]fps={q.fps}[{o}bgf];"
        f"[{o}bgf][{o}fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[{o}]"
    )


def _video_chain(
    src: dict[str, Any],
    motion: dict[str, Any],
    scene_frames: int,
    offset: int,
    q: Quality,
    label_in: str,
    label_out: str,
) -> str:
    w, h = q.width, q.height
    if src["kind"] != "blank" and src.get("layout") == "blur":
        return _blur_chain(src, motion, scene_frames, offset, q, label_in, label_out)
    if src["kind"] == "clip":
        return (
            f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"fps={q.fps},setsar=1,format=yuv420p[{label_out}]"
        )
    if src["kind"] == "blank" or motion["kind"] == MotionKind.STATIC:
        return (
            f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"fps={q.fps},setsar=1,format=yuv420p[{label_out}]"
        )
    z, x, y = _camera(motion["kind"], float(motion["intensity"]), scene_frames, offset)
    big_w, big_h = w * SUPERSAMPLE, h * SUPERSAMPLE
    return (
        f"[{label_in}]scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={q.fps},setsar=1,format=yuv420p[{label_out}]"
    )


def _input_args(src: dict[str, Any], q: Quality, offset_frames: int) -> list[str]:
    if src["kind"] == "blank":
        return ["-f", "lavfi", "-i", f"color=c={BLANK_COLOR}:s={q.width}x{q.height}:r={q.fps}"]
    if src["kind"] == "clip":
        args = ["-stream_loop", "-1"]
        if offset_frames:
            args += ["-ss", f"{offset_frames / q.fps:.3f}"]
        return [*args, "-i", src["path"]]
    return ["-loop", "1", "-framerate", str(q.fps), "-i", src["path"]]


def segment_command(spec: SegmentSpec, q: Quality, out: Path, threads: int = 2) -> list[str]:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", str(threads)]
    cmd += _input_args(spec.source, q, spec.scene_offset)
    blend = spec.blend_frames
    main = spec.frames - blend
    parts = [
        _video_chain(spec.source, spec.motion, spec.scene_frames, spec.scene_offset, q, "0:v", "a")
    ]
    last = "a"
    if blend and spec.next_source is not None and spec.next_motion is not None:
        cmd += _input_args(spec.next_source, q, 0)
        parts.append(
            _video_chain(
                spec.next_source, spec.next_motion, spec.next_scene_frames, 0, q, "1:v", "b"
            )
        )
        parts.append(f"[a]trim=end_frame={spec.frames},setpts=PTS-STARTPTS[a1]")
        parts.append(f"[b]trim=end_frame={blend},setpts=PTS-STARTPTS[b1]")
        parts.append(
            f"[a1][b1]xfade=transition=fade:duration={blend / q.fps:.4f}:offset={main / q.fps:.4f}[x]"
        )
        last = "x"
    post: list[str] = []
    if spec.vignette:
        post.append("vignette=PI/5")
    if spec.grain:
        post.append("noise=alls=6:allf=t")
    if spec.fade_in_frames:
        post.append(f"fade=t=in:st=0:d={spec.fade_in_frames / q.fps:.4f}")
    if spec.fade_out_frames:
        start = max(0, spec.frames - spec.fade_out_frames) / q.fps
        post.append(f"fade=t=out:st={start:.4f}:d={spec.fade_out_frames / q.fps:.4f}")
    post.append(f"trim=end_frame={spec.frames}")
    post.append("setpts=PTS-STARTPTS")
    parts.append(f"[{last}]{','.join(post)}[v]")
    cmd += [
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[v]",
        "-frames:v",
        str(spec.frames),
        "-c:v",
        "libx264",
        "-preset",
        q.preset,
        "-crf",
        str(q.crf),
        "-g",
        str(q.fps * 2),
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(out),
    ]
    return cmd


def segment_key(spec: SegmentSpec, q: Quality) -> str:
    payload = json.dumps([ALGO_VERSION, asdict(spec), asdict(q)], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


OVERLAY_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: top,Arial,{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,{side},{side},{mtop},1
Style: center,Arial,{size_c},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,5,{side},{side},40,1
Style: bottom,Arial,{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,{side},{side},{mbottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def overlay_header(width: int = 1920, height: int = 1080) -> str:
    """Estilos dos textos na tela; no quadro vertical ficam fora da zona coberta pela interface do Shorts."""
    if height > width:
        return OVERLAY_HEADER.format(
            w=width, h=height, size=72, size_c=80, side=60, mtop=260, mbottom=420
        )
    return OVERLAY_HEADER.format(
        w=width, h=height, size=84, size_c=96, side=100, mtop=90, mbottom=90
    )


def overlay_ass(
    texts: list[tuple[float, float, str, str]], width: int = 1920, height: int = 1080
) -> str:
    lines = [overlay_header(width, height).rstrip("\n")]
    for start, end, text, position in texts:
        safe = text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{position},,0,0,0,,{safe}")
    return "\n".join(lines) + "\n"


def _filter_path(path: Path) -> str:
    """Caminho dentro de um filtro ffmpeg (escapa `:`, `'` e `\\`)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def final_command(
    plan: RenderPlan,
    segments_list: Path,
    out: Path,
    overlay_file: Path | None,
    threads: int = 4,
) -> list[str]:
    q = plan.quality
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", str(threads)]
    cmd += ["-f", "concat", "-safe", "0", "-i", str(segments_list)]
    if plan.audio_start:
        cmd += ["-ss", f"{plan.audio_start:.3f}"]
    cmd += ["-i", str(plan.audio)]
    if plan.music:
        cmd += ["-stream_loop", "-1", "-i", str(plan.music)]
    dur = plan.duration
    vf: list[str] = []
    if plan.subtitles_ass:
        vf.append(f"ass='{_filter_path(plan.subtitles_ass)}'")
    if overlay_file:
        vf.append(f"ass='{_filter_path(overlay_file)}'")
    parts: list[str] = []
    fades = (
        f",afade=t=in:st=0:d=0.15,afade=t=out:st={max(dur - 0.25, 0):.3f}:d=0.25"
        if plan.audio_fade
        else ""
    )
    if plan.music:
        parts.append(
            f"[1:a]aresample=48000,apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS{fades}[nar]"
        )
        music = (
            f"[2:a]aresample=48000,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
            f"volume={plan.music_db}dB,afade=t=out:st={max(dur - 2.0, 0):.3f}:d=2[mus]"
        )
        parts.append(music)
        if plan.ducking:
            parts.append("[nar]asplit=2[n1][n2]")
            parts.append(
                "[mus][n1]sidechaincompress=threshold=0.03:ratio=10:attack=30:release=500[duck]"
            )
            parts.append("[n2][duck]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            parts.append("[nar][mus]amix=inputs=2:duration=first:normalize=0[a]")
        amap = "[a]"
    else:
        parts.append(f"[1:a]aresample=48000,apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS{fades}[a]")
        amap = "[a]"
    if vf:
        parts.append(f"[0:v]{','.join(vf)}[v]")
        vmap = "[v]"
        vcodec = ["-c:v", "libx264", "-preset", q.preset, "-crf", str(q.crf), "-pix_fmt", "yuv420p"]
    else:
        vmap = "0:v"
        vcodec = ["-c:v", "copy"]
    cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", amap]
    cmd += [*vcodec, "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}", "-movflags", "+faststart"]
    cmd.append(str(out))
    return cmd


# ----- Planejamento a partir do banco -----


def _stamp(path: Path) -> list[int]:
    st = path.stat()
    return [st.st_mtime_ns, st.st_size]


def _blank(scene_index: int) -> dict[str, Any]:
    return {"kind": "blank", "path": "", "stamp": [0, 0], "scene_index": scene_index}


def _source_for(asset: Asset, scene_index: int) -> dict[str, Any]:
    path = get_settings().data_dir / asset.path
    if not path.is_file():
        raise InvalidState(f"O arquivo da cena {scene_index + 1} não está no disco ({asset.path}).")
    kind = "clip" if asset.type == AssetType.VIDEO_CLIP.value else "image"
    return {"kind": kind, "path": str(path), "stamp": _stamp(path), "scene_index": scene_index}


def music_file(session: Session, timeline: Timeline) -> Path | None:
    if timeline.music.asset_id is None:
        return None
    music_asset = session.get(Asset, timeline.music.asset_id)
    if music_asset is None or not (get_settings().data_dir / music_asset.path).is_file():
        raise InvalidState("O arquivo da trilha sonora não está mais no disco.")
    return get_settings().data_dir / music_asset.path


def plan(
    session: Session,
    video: Video,
    timeline: Timeline,
    quality: Quality,
    subtitles: bool | None = None,
    allow_missing: bool = False,
) -> RenderPlan:
    """Monta o plano de render (sem executar nada). Erros claros em português."""
    assert video.id is not None
    timing = tl_service.collect_timing(session, video)
    tl_service.ensure_valid(session, video, timeline)
    spans = timing.spans
    assets = tl_service.main_assets(session, [sp.scene for sp in spans])
    by_id = {
        a.id: a
        for a in session.exec(
            select(Asset).where(
                Asset.video_id == video.id,
                col(Asset.type).in_([AssetType.IMAGE.value, AssetType.VIDEO_CLIP.value]),
            )
        )
    }
    sources: list[dict[str, Any]] = []
    motions: list[dict[str, Any]] = []
    requested: list[float] = []
    missing: list[int] = []
    for sp in spans:
        edit = timeline.for_scene(sp.scene.index)
        asset = by_id.get(edit.asset_id) if edit and edit.asset_id else assets.get(sp.scene.id or 0)
        if asset is None:
            missing.append(sp.scene.index + 1)
        sources.append(_source_for(asset, sp.scene.index) if asset else _blank(sp.scene.index))
        motions.append(
            edit.motion.model_dump(mode="json")
            if edit
            else {"kind": MotionKind.STATIC.value, "intensity": 0.0}
        )
        requested.append(edit.transition_in.seconds if edit else 0.0)
    if missing and not allow_missing:
        shown = ", ".join(str(m) for m in missing[:12]) + ("…" if len(missing) > 12 else "")
        raise InvalidState(
            f"Faltam imagens nas cenas: {shown}. Envie-as no storyboard ou marque "
            "“preencher com fundo escuro” para um teste."
        )
    starts = [sp.start for sp in spans]
    lengths = [sp.length for sp in spans]
    transitions = clamp_transitions(requested, lengths)
    segments = build_segments(
        sources,
        motions,
        starts,
        timing.duration,
        transitions,
        quality,
        timeline.fade_in_s,
        timeline.fade_out_s,
        timeline.effects.vignette,
        timeline.effects.grain,
    )
    use_subs = timeline.subtitles.enabled if subtitles is None else subtitles
    subs_asset = narration.latest_subtitle(session, video.id, "ass") if use_subs else None
    if use_subs and subs_asset is None:
        raise InvalidState("As legendas pedidas ainda não existem (gere a narração/alinhamento).")
    music_path = music_file(session, timeline)
    texts: list[tuple[float, float, str, str]] = []
    for sp in spans:
        edit = timeline.for_scene(sp.scene.index)
        for t in edit.texts if edit else []:
            start = sp.start + t.start_s
            end = sp.start + (t.end_s if t.end_s is not None else sp.length)
            texts.append((start, min(end, sp.end), t.text, t.position))
    warnings = []
    if timing.partial:
        warnings.append("Narração parcial (teste curto): o vídeo cobre só as cenas já narradas.")
    return RenderPlan(
        quality=quality,
        segments=segments,
        total_frames=round(timing.duration * quality.fps),
        duration=timing.duration,
        audio=narration.asset_path(timing.audio),
        partial=timing.partial,
        subtitles_ass=narration.asset_path(subs_asset) if subs_asset else None,
        overlay_texts=texts,
        music=music_path,
        music_db=timeline.music.volume_db,
        ducking=timeline.music.ducking,
        warnings=warnings,
        missing=missing,
    )


# ----- Execução -----


def render_dir(session: Session, video: Video) -> Path:
    channel = channels.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=True) / "render"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Procs:
    """Processos ffmpeg em andamento (para poder encerrá-los ao cancelar)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen[str]] = set()
        self.cancelled = False

    def run(
        self,
        cmd: list[str],
        timeout: float = 1800,
        tick: Callable[[], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        """Executa o ffmpeg; `tick` (a cada ~5 s) mantém o job vivo e `is_cancelled` permite interromper."""
        with self._lock:
            if self.cancelled:
                raise RenderCancelled
            proc = subprocess.Popen(  # noqa: S603 - comando montado por nós, sem shell
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            self._procs.add(proc)
        started = time.monotonic()
        err = ""
        try:
            while True:
                try:
                    _, err = proc.communicate(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    if self.cancelled or (is_cancelled and is_cancelled()):
                        self.cancel()
                        proc.communicate()
                        raise RenderCancelled from None
                    if time.monotonic() - started > timeout:
                        proc.kill()
                        proc.communicate()
                        raise InvalidState("O ffmpeg demorou demais e foi interrompido.") from None
                    if tick:
                        tick()
        finally:
            with self._lock:
                self._procs.discard(proc)
        if self.cancelled:
            raise RenderCancelled
        if proc.returncode != 0:
            raise InvalidState(f"ffmpeg falhou: {(err or '')[-400:]}")

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            for p in self._procs:
                p.kill()


@dataclass
class RenderResult:
    path: Path
    duration: float
    segments_total: int
    segments_cached: int
    size: int


def default_workers() -> int:
    import os

    return max(1, min(6, (os.cpu_count() or 2) // 2))


def execute(
    plan_: RenderPlan,
    out_dir: Path,
    out_name: str,
    on_progress: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    workers: int | None = None,
) -> RenderResult:
    """Renderiza os segmentos que faltam (em paralelo), junta tudo e escreve `out_dir/out_name`."""
    ffmpeg_ok()
    q = plan_.quality
    seg_dir = out_dir / "segments" / q.name
    seg_dir.mkdir(parents=True, exist_ok=True)
    todo: list[tuple[SegmentSpec, Path]] = []
    paths: list[Path] = []
    for spec in plan_.segments:
        path = seg_dir / f"{segment_key(spec, q)}.mp4"
        paths.append(path)
        if not path.is_file():
            todo.append((spec, path))
    cached = len(plan_.segments) - len(todo)
    total = len(plan_.segments) + 1  # +1 = passo final
    done = cached
    if on_progress:
        on_progress(done, total, f"{cached} segmentos reaproveitados, {len(todo)} para renderizar.")
    procs = _Procs()

    def work(item: tuple[SegmentSpec, Path]) -> int:
        spec, path = item
        tmp = path.with_suffix(".part.mp4")
        procs.run(segment_command(spec, q, tmp))
        tmp.replace(path)
        return spec.position

    pool = ThreadPoolExecutor(max_workers=workers or default_workers())
    try:
        pending: set[Future[int]] = {pool.submit(work, item) for item in todo}
        while pending:
            finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            if is_cancelled and is_cancelled():
                procs.cancel()
                for f in pending:
                    f.cancel()
                raise RenderCancelled
            for f in finished:
                position = f.result()
                done += 1
                if on_progress:
                    on_progress(done, total, f"Cena {position + 1} pronta.")
    except BaseException:
        procs.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    listing = out_dir / f"{out_name}.segments.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
    overlay_file: Path | None = None
    if plan_.overlay_texts:
        overlay_file = out_dir / f"{out_name}.overlay.ass"
        overlay_file.write_text(
            overlay_ass(plan_.overlay_texts, q.width, q.height), encoding="utf-8"
        )
    out = out_dir / out_name
    tmp_out = out.with_suffix(".part.mp4")
    if on_progress:
        on_progress(done, total, "Juntando vídeo e áudio…")
    final = _Procs()
    try:
        final.run(
            final_command(plan_, listing, tmp_out, overlay_file),
            timeout=7200,
            tick=lambda: on_progress(done, total, "") if on_progress else None,
            is_cancelled=is_cancelled,
        )
        tmp_out.replace(out)
    finally:
        listing.unlink(missing_ok=True)
        if overlay_file:
            overlay_file.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
    # remove segmentos que não pertencem mais ao plano (imagem/timeline mudou)
    keep = {p.name for p in paths}
    for old in seg_dir.glob("*.mp4"):
        if old.name not in keep:
            old.unlink(missing_ok=True)
    if on_progress:
        on_progress(total, total, "Render concluído.")
    return RenderResult(out, plan_.duration, len(plan_.segments), cached, out.stat().st_size)


# ----- Resultado como ativo -----


def latest_render(session: Session, video_id: int, kind: str | None = None) -> Asset | None:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.RENDER.value)
        .order_by(col(Asset.id).desc())
    )
    for a in session.exec(stmt):
        if kind is None or a.meta.get("kind") == kind:
            return a
    return None


def store_result(
    session: Session,
    video: Video,
    result: RenderResult,
    kind: str,
    meta: dict[str, Any],
    job_id: int | None,
) -> Asset:
    """Registra o render; mantém só o último de cada tipo (prévia/final) para poupar disco."""
    assert video.id is not None
    old = list(
        session.exec(
            select(Asset).where(Asset.video_id == video.id, Asset.type == AssetType.RENDER.value)
        )
    )
    for a in old:
        if a.meta.get("kind") == kind:
            (get_settings().data_dir / a.path).unlink(missing_ok=True)
            session.delete(a)
    asset = Asset(
        video_id=video.id,
        type=AssetType.RENDER.value,
        path=narration.relpath(result.path),
        meta={
            "kind": kind,
            "duration": round(result.duration, 2),
            "bytes": result.size,
            "segments": result.segments_total,
            "segments_cached": result.segments_cached,
            "created_unix": int(time.time()),
            **meta,
        },
        based_on_script_version_id=video.approved_script_version_id,
        job_id=job_id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


# ----- Job -----


def start(
    session: Session,
    video_id: int,
    actor: User,
    quality: str = "preview",
    subtitles: bool | None = None,
    allow_missing: bool = False,
) -> Job:
    """Valida tudo que dá para validar agora (erros claros) e enfileira o job de render."""
    from canaisdark.models import JobType
    from canaisdark.services import jobs

    ffmpeg_ok()
    if quality not in QUALITIES:
        raise InvalidState("Qualidade inválida. Use: preview ou final.")
    video = videos.get_video(session, video_id)
    asset, tl = tl_service.ensure_timeline(session, video_id, f"user:{actor.id}")
    plan(session, video, tl, QUALITIES[quality], subtitles, allow_missing)  # só valida
    return jobs.create_job(
        session,
        video_id,
        JobType.RENDER,
        {
            "quality": quality,
            "subtitles": subtitles,
            "allow_missing": allow_missing,
            "timeline_asset_id": asset.id,
        },
        actor,
    )


def run_job(session: Session, job: Job) -> None:
    """Corpo do job (chamado pelo worker): planeja, executa com progresso e guarda o resultado."""
    from canaisdark.services import jobs

    assert job.id is not None
    p = job.params
    video = videos.get_video(session, job.video_id)
    asset = session.get(Asset, p["timeline_asset_id"])
    if asset is None:
        raise InvalidState("A timeline deste render não existe mais.")
    tl = tl_service.load(asset)
    quality = QUALITIES[p["quality"]]
    rplan = plan(session, video, tl, quality, p.get("subtitles"), bool(p.get("allow_missing")))
    for w in rplan.warnings:
        jobs.append_log(session, job.id, f"Aviso: {w}")
    jobs.append_log(
        session,
        job.id,
        f"Render {quality.name} {quality.width}x{quality.height}@{quality.fps}: "
        f"{len(rplan.segments)} cenas, {rplan.duration:.1f}s.",
    )
    last_logged = [""]

    def progress(done: int, total: int, message: str) -> None:
        jobs.heartbeat(session, job.id or 0, int(done * 100 / max(total, 1)))
        if message and message != last_logged[0]:
            last_logged[0] = message
            jobs.append_log(session, job.id or 0, message)

    inputs = tl_service.inputs_hash(session, video)
    started = time.monotonic()
    result = execute(
        rplan,
        render_dir(session, video),
        f"{quality.name}-{int(time.time())}.mp4",
        on_progress=progress,
        is_cancelled=lambda: jobs.is_cancelled(session, job.id or 0),
    )
    out = store_result(
        session,
        video,
        result,
        quality.name,
        {
            "timeline_asset_id": asset.id,
            "inputs_hash": inputs,
            "subtitles": rplan.subtitles_ass is not None,
            "music": rplan.music is not None,
            "partial": rplan.partial,
            "width": quality.width,
            "height": quality.height,
            "fps": quality.fps,
            "missing": rplan.missing,
        },
        job.id,
    )
    elapsed = time.monotonic() - started
    jobs.append_log(
        session,
        job.id,
        f"Pronto em {elapsed:.0f}s: {result.size / 1e6:.1f} MB ({result.segments_cached} cenas reaproveitadas).",
    )
    jobs.complete(session, job.id, {"asset_id": out.id, "seconds": round(elapsed, 1)})


def approve(session: Session, video_id: int, actor: User) -> Asset:
    """Revisão humana: marca o render final mais recente como aprovado."""
    asset = latest_render(session, video_id, "final")
    if asset is None:
        raise InvalidState("Não há render final para aprovar. Gere o render final primeiro.")
    asset.meta = {
        **asset.meta,
        "approved_by": actor.id,
        "approved_at": int(time.time()),
    }
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset
