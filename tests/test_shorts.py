from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.schemas.shorts import ShortIn, ShortsPlan
from canaisdark.services import shorts, videos
from canaisdark.services.errors import InvalidState
from tests.media import setup_video

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
