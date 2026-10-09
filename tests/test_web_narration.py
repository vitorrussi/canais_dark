from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import FakeElevenLabs
from canaisdark.models import AssetType, User
from canaisdark.schemas.script import SceneIn
from canaisdark.services import credits, jobs, narration, scripts
from canaisdark.worker.runner import Worker


@pytest.fixture
def ctx(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "KEY")
    fake = FakeElevenLabs(credits=10_000)
    credits.set_client_factory(lambda: fake)
    client.post("/login", data={"token": user_token[1]})
    client.post("/channels", data={"name": "Canal", "elevenlabs_voice_id": "voz1"})
    r = client.post("/channels/canal/videos", data={"topic": "t"})
    vid = int(str(r.url).rsplit("/", 1)[1])
    yield_ctx = {"client": client, "vid": vid, "fake": fake, "user": user_token[0]}
    yield yield_ctx  # type: ignore[misc]
    credits.set_client_factory(None)


def _approve(session: Session, ctx: dict[str, object]) -> None:
    scripts.save_user_script(
        session,
        ctx["vid"],
        [SceneIn(text="Primeira cena aqui."), SceneIn(text="Segunda cena aqui.")],
        ctx["user"],
        approve=True,
    )  # type: ignore[arg-type]


def test_card_requires_approved_script(ctx: dict[str, object]) -> None:
    html = ctx["client"].get(f"/videos/{ctx['vid']}").text  # type: ignore[attr-defined]
    assert "Aprove o roteiro para liberar a narração" in html and "ElevenLabs ✓" in html


def test_card_shows_estimates_and_balance(session: Session, ctx: dict[str, object]) -> None:
    _approve(session, ctx)
    html = ctx["client"].get(f"/videos/{ctx['vid']}").text  # type: ignore[attr-defined]
    assert (
        "Teste curto" in html
        and "saldo: 10.000 créditos" in html
        and 'id="est-credits">10<' in html
    )  # 18 chars × 0,5 → 9? ver abaixo
    assert "narration.js" in html and "data-cum" in html


def test_start_via_site_and_full_flow(session: Session, ctx: dict[str, object]) -> None:
    _approve(session, ctx)
    c = ctx["client"]
    r = c.post(
        f"/videos/{ctx['vid']}/narration/start",
        data={"mode": "test", "test_scenes": "1", "align": "off"},
    )  # type: ignore[attr-defined]
    assert "Narração enfileirada" in r.text and "Na fila" in r.text
    assert 'hx-trigger="every 3s"' in c.get(f"/videos/{ctx['vid']}/narration/card").text  # type: ignore[attr-defined]
    Worker(make_client=lambda: ctx["fake"], poll_seconds=0).run_once()  # type: ignore[arg-type,return-value]
    card = c.get(f"/videos/{ctx['vid']}/narration/card").text  # type: ignore[attr-defined]
    assert (
        "<audio" in card
        and "só teste" in card
        and "estimados" in card
        and 'hx-trigger="every 3s"' not in card
    )
    audio = narration.latest_asset(session, ctx["vid"], AssetType.AUDIO)  # type: ignore[arg-type]
    assert audio
    served = c.get(f"/assets/{audio.id}/file", headers={"Range": "bytes=0-9"})  # type: ignore[attr-defined]
    assert (
        served.status_code == 206
        and len(served.content) == 10
        and served.headers["content-type"] == "audio/mpeg"
    )
    dl = c.get(f"/assets/{audio.id}/file?download=1")  # type: ignore[attr-defined]
    assert "attachment" in dl.headers["content-disposition"]
    srt = narration.latest_subtitle(session, ctx["vid"], "srt")  # type: ignore[arg-type]
    assert srt and "-->" in c.get(f"/assets/{srt.id}/file").text  # type: ignore[attr-defined]
    assert "Tempo por cena" in card and "Legenda (.srt)" in card


def test_start_blocked_shows_reason(
    ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    r = ctx["client"].post(f"/videos/{ctx['vid']}/narration/start", data={"mode": "test"})  # type: ignore[attr-defined]
    assert "não foi aprovado" in r.text


def test_cancel_retry_and_jobs_page(session: Session, ctx: dict[str, object]) -> None:
    _approve(session, ctx)
    c = ctx["client"]
    c.post(f"/videos/{ctx['vid']}/narration/start", data={"mode": "test", "align": "off"})  # type: ignore[attr-defined]
    job = jobs.latest_job(session, ctx["vid"], jobs.JobType.TTS)  # type: ignore[arg-type]
    assert job and job.id
    assert "Job cancelado" in c.post(f"/jobs/{job.id}/cancel").text  # type: ignore[attr-defined]
    assert "Tentar de novo" in c.get("/jobs").text  # type: ignore[attr-defined]
    assert "enfileirado" in c.post(f"/jobs/{job.id}/retry").text  # type: ignore[attr-defined]
    assert "Job cancelado" not in c.get("/jobs").text and "#2" in c.get("/jobs").text  # type: ignore[attr-defined]


def test_asset_file_rejects_path_escape(session: Session, ctx: dict[str, object]) -> None:
    from canaisdark.models import Asset

    a = Asset(video_id=ctx["vid"], type="audio", path="../../etc/passwd")  # type: ignore[arg-type]
    session.add(a)
    session.commit()
    assert ctx["client"].get(f"/assets/{a.id}/file").status_code == 404  # type: ignore[attr-defined]
