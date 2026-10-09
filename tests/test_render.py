import json
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, User
from canaisdark.schemas.timeline import TextOverlay
from canaisdark.services import narration, render, subtitles, timeline, videos
from canaisdark.services.errors import InvalidState
from tests.media import have_ffmpeg, make_audio, setup_video

TINY = render.Quality("tiny", 320, 180, 12, "ultrafast", 32)
needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg não instalado")


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return tmp_path


def test_clamp_transitions() -> None:
    assert render.clamp_transitions([0, 0.6, 0.6, 0.6], [5, 4, 1, 6])[:2] == [0.0, 0.6]
    out = render.clamp_transitions([0, 2.0, 2.0], [1.0, 1.0, 1.0])
    assert out[0] == 0.0 and out[1] <= 1.8 and out[1] + out[2] <= 1.8  # metades não passam de 90%
    assert render.clamp_transitions([0, 0.05], [5, 5]) == [0.0, 0.0]  # curtíssima vira corte


def test_segments_cover_exact_frames() -> None:
    starts = [0.0, 5.0, 9.0, 10.0]
    total = 16.8
    tr = render.clamp_transitions([0, 0.6, 0.6, 0.0], [5.0, 4.0, 1.0, 6.8])
    src = [
        {"kind": "image", "path": f"/x/{i}.jpg", "stamp": [1, 1], "scene_index": i}
        for i in range(4)
    ]
    mot = [{"kind": "zoom_in", "intensity": 0.1}] * 4
    segs = render.build_segments(src, mot, starts, total, tr, render.FINAL, 0.6, 0.8, True, False)
    assert sum(s.frames for s in segs) == round(total * render.FINAL.fps)
    assert segs[0].fade_in_frames and segs[-1].fade_out_frames and not segs[1].fade_in_frames
    assert segs[0].blend_frames == round(0.6 * 30) and segs[2].blend_frames == 0
    assert segs[1].scene_offset == segs[0].blend_frames  # cena 2 continua de onde o crossfade parou


def test_segment_command_and_cache_key() -> None:
    src = {"kind": "image", "path": "/x/a.jpg", "stamp": [1, 2], "scene_index": 0}
    nxt = {"kind": "image", "path": "/x/b.jpg", "stamp": [3, 4], "scene_index": 1}
    spec = render.SegmentSpec(
        0,
        0,
        src,
        {"kind": "pan_left", "intensity": 0.08},
        100,
        120,
        0,
        18,
        nxt,
        {"kind": "zoom_out", "intensity": 0.1},
        130,
        vignette=True,
    )
    cmd = " ".join(render.segment_command(spec, render.PREVIEW, Path("/tmp/o.mp4")))
    assert "zoompan" in cmd and "xfade" in cmd and "vignette" in cmd and "-an" in cmd
    assert "/x/a.jpg" in cmd and "/x/b.jpg" in cmd
    k1 = render.segment_key(spec, render.PREVIEW)
    assert k1 == render.segment_key(spec, render.PREVIEW)
    assert k1 != render.segment_key(spec, render.FINAL)
    changed = render.SegmentSpec(**{**spec.__dict__, "next_source": {**nxt, "stamp": [9, 9]}})
    assert render.segment_key(changed, render.PREVIEW) != k1  # imagem da próxima mudou -> refaz


def test_plan_requires_images(session: Session, user_token: tuple[User, str], cfg: Path) -> None:
    ids = setup_video(session, user_token[0], [2.0, 2.0], images=False)
    video = videos.get_video(session, ids["video_id"])
    tl = timeline.build_default(session, video)
    with pytest.raises(InvalidState, match="Faltam imagens nas cenas: 1, 2"):
        render.plan(session, video, tl, TINY)
    p = render.plan(session, video, tl, TINY, allow_missing=True)
    assert [s.source["kind"] for s in p.segments] == ["blank", "blank"] and p.missing == [1, 2]


def test_final_command_music_ducking_and_subs(
    session: Session, user_token: tuple[User, str], cfg: Path
) -> None:
    ids = setup_video(session, user_token[0], [2.0, 2.0])
    video = videos.get_video(session, ids["video_id"])
    tl = timeline.build_default(session, video)
    p = render.plan(session, video, tl, TINY)
    p.music, p.ducking = Path("/m/song.mp3"), True
    p.subtitles_ass = Path("/s/it's: a.ass")
    cmd = " ".join(render.final_command(p, Path("/l.txt"), Path("/o.mp4"), None))
    assert "sidechaincompress" in cmd and "-stream_loop -1" in cmd and "libx264" in cmd
    assert "ass='/s/it\\'s\\: a.ass'" in cmd
    p.music, p.subtitles_ass = None, None
    plain = " ".join(render.final_command(p, Path("/l.txt"), Path("/o.mp4"), None))
    assert "-c:v copy" in plain and "sidechain" not in plain


def probe(path: Path) -> dict[str, object]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,nb_frames",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)  # type: ignore[no-any-return]


@needs_ffmpeg
def test_real_render_cache_and_streams(
    session: Session, user_token: tuple[User, str], cfg: Path
) -> None:
    ids = setup_video(session, user_token[0], [2.0, 2.0, 2.0], real_audio=True)
    video = videos.get_video(session, ids["video_id"])
    tl = timeline.build_default(session, video)
    out_dir = render.render_dir(session, video)
    p = render.plan(session, video, tl, TINY)
    res = render.execute(p, out_dir, "a.mp4", workers=2)
    info = probe(res.path)
    kinds = sorted(s["codec_type"] for s in info["streams"])  # type: ignore[index,attr-defined]
    assert kinds == ["audio", "video"]
    assert float(info["format"]["duration"]) == pytest.approx(6.8, abs=0.25)  # type: ignore[index]
    assert res.segments_total == 3 and res.segments_cached == 0
    again = render.execute(render.plan(session, video, tl, TINY), out_dir, "b.mp4", workers=2)
    assert again.segments_cached == 3  # nada refeito
    # trocar a imagem da cena 2 invalida o segmento 1 (a imagem aparece nele) e o 0 (crossfade com ela)
    img = next(
        a
        for a in session.exec(__import__("sqlmodel").select(Asset))
        if a.scene_id and a.path.endswith("s001_main.jpg")
    )
    path = get_settings().data_dir / img.path
    from PIL import Image

    Image.new("RGB", (320, 180), (1, 2, 3)).save(path)
    third = render.execute(render.plan(session, video, tl, TINY), out_dir, "c.mp4", workers=2)
    assert third.segments_cached == 1


@needs_ffmpeg
def test_real_render_with_subtitles_texts_and_music(
    session: Session, user_token: tuple[User, str], cfg: Path
) -> None:
    ids = setup_video(session, user_token[0], [2.0, 2.0], real_audio=True)
    video = videos.get_video(session, ids["video_id"])
    words = [{"text": "Olá", "start": 0.2, "end": 0.6}, {"text": "mundo", "start": 0.7, "end": 1.2}]
    ass_path = get_settings().data_dir / "x" / "n.ass"
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(subtitles.to_ass(words), encoding="utf-8")
    session.add(
        Asset(
            video_id=ids["video_id"],
            type=AssetType.SUBTITLE.value,
            path=narration.relpath(ass_path),
            meta={"format": "ass"},
        )
    )
    music = get_settings().data_dir / "x" / "m.mp3"
    make_audio(music, 3.0)
    m = Asset(
        video_id=ids["video_id"], type=AssetType.MUSIC.value, path=narration.relpath(music), meta={}
    )
    session.add(m)
    session.commit()
    tl = timeline.build_default(session, video)
    tl.scenes[0].texts.append(TextOverlay(text="1900", position="top"))
    tl.music.asset_id = m.id
    tl.subtitles.enabled = True
    p = render.plan(session, video, tl, TINY)
    assert p.subtitles_ass and p.music and p.overlay_texts[0][2] == "1900"
    res = render.execute(p, render.render_dir(session, video), "s.mp4", workers=2)
    kinds = sorted(s["codec_type"] for s in probe(res.path)["streams"])  # type: ignore[index,attr-defined]
    assert kinds == ["audio", "video"]


@needs_ffmpeg
def test_render_job_through_worker(
    session: Session, user_token: tuple[User, str], cfg: Path
) -> None:
    from canaisdark.models import JobStatus
    from canaisdark.services import jobs
    from canaisdark.worker.runner import Worker

    ids = setup_video(session, user_token[0], [2.0, 2.0], real_audio=True)
    job = render.start(session, ids["video_id"], user_token[0], "preview")
    assert job.type == "render" and job.params["quality"] == "preview"
    with pytest.raises(Exception, match="em andamento"):
        render.start(session, ids["video_id"], user_token[0], "preview")
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    done = jobs.get_job(session, job.id)  # type: ignore[arg-type]
    assert done.status == JobStatus.DONE.value, done.error + done.log
    out = render.latest_render(session, ids["video_id"], "preview")
    assert out and (get_settings().data_dir / out.path).is_file()
    assert out.meta["kind"] == "preview" and out.meta["width"] == 1280
    # novo render do mesmo tipo substitui o arquivo antigo
    old_path = get_settings().data_dir / out.path
    j2 = render.start(session, ids["video_id"], user_token[0], "preview")
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    assert jobs.get_job(session, j2.id).status == JobStatus.DONE.value  # type: ignore[arg-type]
    assert not old_path.exists() and render.latest_render(session, ids["video_id"], "preview")


def test_render_start_validations(
    session: Session, user_token: tuple[User, str], cfg: Path
) -> None:
    ids = setup_video(session, user_token[0], [2.0], images=False)
    with pytest.raises(InvalidState, match="Faltam imagens"):
        render.start(session, ids["video_id"], user_token[0], "preview")
    with pytest.raises(InvalidState, match="Qualidade inválida"):
        render.start(session, ids["video_id"], user_token[0], "8k")
