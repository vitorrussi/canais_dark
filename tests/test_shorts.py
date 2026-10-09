from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, JobStatus, User
from canaisdark.schemas.shorts import ShortIn, ShortsPlan
from canaisdark.services import jobs, render, shorts, shorts_render, video_status, videos
from canaisdark.services.errors import InvalidState
from tests.media import have_ffmpeg, setup_video

TEXT = (
    "o farol ficava no alto da colina e ninguém sabia quem o acendia todas as noites "
    "até que uma criança resolveu subir a escada de pedra para descobrir"
)


def _words(text: str, step: float = 0.5, start: float = 0.0) -> list[dict[str, object]]:
    out = []
    t = start
    for tok in text.split():
        out.append({"text": tok, "start": round(t, 3), "end": round(t + step * 0.9, 3)})
        t += step
    return out


def test_locate_exact_with_padding() -> None:
    words = _words("antes disso nada " + TEXT + " e depois veio a chuva forte", start=0.0)
    found = shorts.locate(words, TEXT.replace("farol", "Farol,"))  # caixa/pontuação não importam
    assert found is not None and found.confidence == 1.0
    assert 1.4 <= found.start <= 1.5  # não invade a palavra anterior
    assert found.end > found.start


def test_locate_tolerates_small_divergence_and_rejects_others() -> None:
    words = _words(TEXT)
    near = TEXT.replace("criança", "menina")
    found = shorts.locate(words, near)
    assert found is not None and found.confidence >= 0.9
    assert (
        shorts.locate(words, "texto que nunca foi dito em nenhum lugar da narração gravada") is None
    )
    assert shorts.locate(words, "curto demais") is None


def test_schema_normalizes_and_limits() -> None:
    s = ShortIn(exact_text="  " + TEXT + "  ", title="  Meu   título ", hook_override="  ")
    assert s.title == "Meu título" and s.hook_override is None
    with pytest.raises(ValidationError):
        ShortIn(exact_text="curto", title="x")
    assert ShortsPlan().skipped is False


@pytest.fixture
def ids(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return setup_video(session, user_token[0], [10.0, 10.0, 10.0, 10.0])


def test_build_plan_from_estimated_scene_times(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    ok = ShortIn(exact_text="Cena número 1. Cena número 2.", title="Abertura")
    plan = shorts.build_plan(session, video, [ok], "user")
    assert plan.timing == "estimated"
    s = plan.shorts[0]
    assert s.start_s == 0.0 and 19.5 < s.end_s < 21 and s.located == "exact"


def test_build_plan_errors_are_listed(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    short = ShortIn(exact_text="Cena número 1. Cena número 1. Cena", title="Curto")
    gone = ShortIn(exact_text="Nada disso foi narrado em momento algum aqui", title="Fantasma")
    with pytest.raises(InvalidState) as exc:
        shorts.build_plan(session, video, [short, gone], "user")
    msg = str(exc.value)
    assert "Short 2" in msg and "não foi encontrado" in msg
    a = ShortIn(exact_text="Cena número 1. Cena número 2.", title="A")
    b = ShortIn(exact_text="Cena número 2. Cena número 3.", title="B")
    with pytest.raises(InvalidState, match="sobrepõem"):
        shorts.build_plan(session, video, [a, b], "user")


def test_versions_and_skip(session: Session, ids: dict[str, int]) -> None:
    vid = ids["video_id"]
    item = ShortIn(exact_text="Cena número 1. Cena número 2.", title="Abertura")
    a1, _ = shorts.save_selections(session, vid, [item], "claude", "claude")
    assert a1.meta["number"] == 1 and a1.meta["count"] == 1
    plan = shorts.set_skipped(session, vid, True, "user:1")
    assert plan.skipped and len(plan.shorts) == 1  # mantém os trechos
    asset, loaded = shorts.latest_plan(session, vid)  # type: ignore[misc]
    assert asset.meta["number"] == 2 and loaded.skipped
    assert shorts.set_skipped(session, vid, False, "user:1").skipped is False


# ----- render -----

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg não instalado")
VTINY = render.Quality("preview", 180, 320, 12, "ultrafast", 32)


@pytest.fixture
def tiny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(render.SHORT_QUALITIES, "preview", VTINY)


def _stage(session: Session, video_id: int) -> str:
    video = videos.get_video(session, video_id)
    return next(s.state for s in video_status.get_stages(session, video) if s.key == "shorts")


def test_plan_short_uses_blur_then_fill_for_vertical_variant(
    session: Session, ids: dict[str, int], tmp_path: Path, tiny: None
) -> None:
    vid = ids["video_id"]
    shorts.save_selections(
        session,
        vid,
        [ShortIn(exact_text="Cena número 2. Cena número 3.", title="Meio")],
        "user",
        "u",
    )
    video = videos.get_video(session, vid)
    _, plan = shorts.latest_plan(session, vid)  # type: ignore[misc]
    sel = plan.shorts[0]
    rp = shorts_render.plan_short(session, video, sel, 0, VTINY, tmp_path / "o")
    assert rp.audio_start == pytest.approx(sel.start_s) and rp.audio_fade
    assert rp.duration == pytest.approx(sel.end_s - sel.start_s)
    assert {s.source["layout"] for s in rp.segments} == {"blur"}
    assert rp.subtitles_ass and rp.subtitles_ass.read_text().count("Dialogue") >= 6
    # imagem vertical da primeira cena passa a ser usada em `fill`
    img = next(a for a in session.exec(select(Asset)) if a.path.endswith("s001_main.jpg"))
    img.meta = {**img.meta, "variant": "vertical", "width": 180, "height": 320}
    session.add(img)
    session.commit()
    rp2 = shorts_render.plan_short(session, video, sel, 0, VTINY, tmp_path / "o")
    assert rp2.segments[0].source["layout"] == "fill"


@needs_ffmpeg
def test_shorts_job_renders_and_replaces(
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny: None,
) -> None:
    from canaisdark.worker.runner import Worker

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ids = setup_video(session, user_token[0], [10.0, 10.0, 10.0, 10.0], real_audio=True)
    vid = ids["video_id"]
    assert _stage(session, vid) == "not_started"
    with pytest.raises(InvalidState, match="Ainda não há Shorts"):
        shorts_render.start(session, vid, user_token[0])
    item = ShortIn(
        exact_text="Cena número 1. Cena número 2.", title="Abertura", hook_override="O FAROL"
    )
    shorts.save_selections(session, vid, [item], "claude", "claude")
    assert _stage(session, vid) == "pending"
    job = shorts_render.start(session, vid, user_token[0], "preview")
    assert job.type == "shorts"
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    done = jobs.get_job(session, job.id)  # type: ignore[arg-type]
    assert done.status == JobStatus.DONE.value, done.error + done.log
    out = shorts_render.best_for(session, vid)[0]
    path = get_settings().data_dir / out.path
    assert path.is_file() and out.meta["width"] == 180 and out.type == AssetType.SHORT.value
    assert _stage(session, vid) == "done"
    j2 = shorts_render.start(session, vid, user_token[0], "preview", index=0)
    Worker(poll_seconds=0).run_once()
    session.expire_all()
    assert jobs.get_job(session, j2.id).status == JobStatus.DONE.value  # type: ignore[arg-type]
    assert not path.exists() and len(shorts_render.list_shorts(session, vid)) == 1


def test_stage_is_optional_and_skippable(session: Session, ids: dict[str, int]) -> None:
    vid = ids["video_id"]
    shorts.set_skipped(session, vid, True, "u")
    assert _stage(session, vid) == "skipped"
    shorts.set_skipped(session, vid, False, "u")
    assert _stage(session, vid) == "not_started"
