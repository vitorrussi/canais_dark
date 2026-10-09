from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import ElevenLabsError, FakeElevenLabs
from canaisdark.models import User
from canaisdark.services import credits, voices


@pytest.fixture
def ctx(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "KEY")
    fake = FakeElevenLabs()
    voices.set_client_factory(lambda: fake)
    credits.set_client_factory(lambda: fake)
    monkeypatch.setattr("canaisdark.web.voices.default_client", lambda: fake)
    client.post("/login", data={"token": user_token[1]})
    client.post("/channels", data={"name": "Canal"})
    yield {"client": client, "fake": fake}  # type: ignore[misc]
    voices.set_client_factory(None)
    credits.set_client_factory(None)


def test_account_and_library_tabs(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["client"]  # type: ignore[assignment]
    html = c.get("/channels/canal/voice").text
    assert (
        "George (demo)" in html
        and "Usar esta voz" in html
        and "Flash v2.5" in html
        and "Multilingual v2" in html
    )
    lib = c.get("/channels/canal/voice?tab=library&accent=brazilian").text
    assert (
        "Adriano (demo)" in lib
        and "exige plano pago" in lib.replace("<strong>", "").replace("</strong>", "")
        or "plano pago" in lib
    )
    assert "Usar esta voz</button>" not in lib  # plano free: não dá para usar voz da biblioteca


def test_library_usable_on_paid_plan(ctx: dict[str, object]) -> None:
    ctx["fake"].tier = "starter"  # type: ignore[attr-defined]
    credits.get_balance(force=True)
    lib = ctx["client"].get("/channels/canal/voice?tab=library").text  # type: ignore[attr-defined]
    assert "Usar esta voz</button>" in lib


def test_select_voice_and_model(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["client"]  # type: ignore[assignment]
    r = c.post(
        "/channels/canal/voice/select",
        data={"voice_id": "fake-george", "model_id": "eleven_multilingual_v2"},
    )
    assert (
        "Voz e modelo do canal atualizados" in r.text
        and "fake-george" in r.text
        and "eleven_multilingual_v2" in r.text
    )
    page = c.get("/channels/canal?tab=settings").text
    assert 'value="fake-george"' in page and "Escolher voz e modelo" in page


def test_sample_returns_audio_and_validates(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["client"]  # type: ignore[assignment]
    r = c.post(
        "/channels/canal/voice/sample",
        json={"voice_id": "fake-george", "model_id": "eleven_flash_v2_5", "text": "Abril de 1986."},
    )
    assert (
        r.status_code == 200
        and r.headers["content-type"] == "audio/mpeg"
        and r.headers["x-credits-estimated"] == "7"
        and len(r.content) > 1000
    )
    long = c.post(
        "/channels/canal/voice/sample", json={"voice_id": "v", "model_id": "m", "text": "x" * 151}
    )
    assert long.status_code == 422 and "limitado" in long.json()["detail"]
    fake: FakeElevenLabs = ctx["fake"]  # type: ignore[assignment]

    def boom(*a: object, **k: object) -> bytes:
        raise ElevenLabsError("Recurso/voz indisponível no seu plano. Use uma voz premade.", 402)

    fake.text_to_speech = boom  # type: ignore[method-assign]
    bad = c.post(
        "/channels/canal/voice/sample", json={"voice_id": "lib", "model_id": "m", "text": "oi"}
    )
    assert bad.status_code == 422 and "premade" in bad.json()["detail"]


def test_requires_key_and_login(client: TestClient, user_token: tuple[User, str]) -> None:
    assert client.get("/channels/x/voice", follow_redirects=False).status_code == 303
    client.post("/login", data={"token": user_token[1]})
    client.post("/channels", data={"name": "Canal"})
    assert "ELEVENLABS_API_KEY não está configurada" in client.get("/channels/canal/voice").text
    assert (
        client.post(
            "/channels/canal/voice/sample", json={"voice_id": "v", "model_id": "m", "text": "oi"}
        ).status_code
        == 422
    )
