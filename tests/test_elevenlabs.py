import json

import httpx
import pytest

from canaisdark.integrations.elevenlabs import (
    ElevenLabsClient,
    ElevenLabsError,
    FakeElevenLabs,
    credit_multiplier,
    mp3_duration_seconds,
)


def client(handler: httpx.MockTransport) -> tuple[ElevenLabsClient, list[float]]:
    sleeps: list[float] = []
    return ElevenLabsClient("KEY", transport=handler, sleep=sleeps.append), sleeps


def test_requires_key() -> None:
    with pytest.raises(ElevenLabsError, match="ELEVENLABS_API_KEY"):
        ElevenLabsClient("")


def test_tts_request_shape_and_header() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers["xi-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=b"MP3DATA")

    c, _ = client(httpx.MockTransport(handler))
    audio = c.text_to_speech(
        "v1",
        "Olá",
        model_id="eleven_flash_v2_5",
        stability=0.4,
        similarity=0.7,
        speed=1.0,
        previous_text="antes",
    )
    assert audio == b"MP3DATA" and seen["key"] == "KEY"
    assert "/v1/text-to-speech/v1/stream" in str(
        seen["url"]
    ) and "output_format=mp3_44100_128" in str(seen["url"])
    body = seen["body"]
    assert (
        isinstance(body, dict)
        and body["model_id"] == "eleven_flash_v2_5"
        and body["previous_text"] == "antes"
        and "next_text" not in body
    )
    assert body["voice_settings"] == {"stability": 0.4, "similarity_boost": 0.7, "speed": 1.0}


def test_retry_on_429_then_success() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return (
            httpx.Response(
                429, json={"detail": {"status": "too_many_concurrent_requests", "message": "x"}}
            )
            if calls["n"] < 3
            else httpx.Response(200, content=b"ok")
        )

    c, sleeps = client(httpx.MockTransport(handler))
    assert c.text_to_speech("v", "t", model_id="m", stability=0.5, similarity=0.5, speed=1) == b"ok"
    assert calls["n"] == 3 and sleeps == [2.0, 4.0]


def test_retry_exhausted_raises_retryable() -> None:
    c, _ = client(httpx.MockTransport(lambda r: httpx.Response(503, text="down")))
    with pytest.raises(ElevenLabsError) as e:
        c.text_to_speech("v", "t", model_id="m", stability=0.5, similarity=0.5, speed=1)
    assert e.value.retryable and "instável" in str(e.value)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            401,
            {"detail": {"status": "invalid_api_key", "message": "bad"}},
            "Chave do ElevenLabs inválida",
        ),
        (
            401,
            {
                "detail": {
                    "status": "detected_unusual_activity",
                    "message": "Unusual activity detected. Free Tier usage disabled",
                }
            },
            "VPN",
        ),
        (402, {"detail": {"status": "payment_required", "message": "paid plan"}}, "voz premade"),
        (404, {"detail": {"status": "voice_not_found", "message": "x"}}, "voice_id"),
        (422, {"detail": [{"msg": "campo inválido"}]}, "campo inválido"),
        (
            400,
            {"detail": {"status": "quota_exceeded", "message": "You have 10 credits remaining"}},
            "Créditos",
        ),
    ],
)
def test_error_mapping_not_retried(status: int, body: dict[str, object], expected: str) -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json=body)

    c, _ = client(httpx.MockTransport(handler))
    with pytest.raises(ElevenLabsError, match=expected):
        c.forced_alignment(b"x", "t")
    assert calls["n"] == 1


def test_alignment_multipart_and_subscription() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/forced-alignment":
            assert b'name="text"' in req.content and b'name="file"' in req.content
            return httpx.Response(
                200, json={"words": [{"text": "a", "start": 0, "end": 1, "loss": 0.1}], "loss": 0.1}
            )
        return httpx.Response(200, json={"character_count": 120, "character_limit": 10000})

    c, _ = client(httpx.MockTransport(handler))
    assert c.forced_alignment(b"audio", "a")["words"][0]["text"] == "a"
    sub = c.subscription()
    assert sub and sub.remaining == 9880


def test_subscription_none_without_permission() -> None:
    c, _ = client(
        httpx.MockTransport(
            lambda r: httpx.Response(
                401, json={"detail": {"status": "missing_permissions", "message": "x"}}
            )
        )
    )
    assert c.subscription() is None


def test_fake_and_helpers() -> None:
    assert (
        credit_multiplier("eleven_flash_v2_5") == 0.5
        and credit_multiplier("eleven_multilingual_v2") == 1.0
    )
    f = FakeElevenLabs(credits=1000, high_loss_words={"farol"})
    audio = f.text_to_speech("v", "O farol apagou. " * 3, model_id="eleven_flash_v2_5")
    assert f.used == 24 and abs(mp3_duration_seconds(audio) - 48 / 15) < 0.1
    data = f.forced_alignment(audio, "O farol apagou.")
    assert [w["text"] for w in data["words"]] == ["O", "farol", "apagou."]
    assert data["words"][1]["loss"] == 0.9 and data["words"][-1]["end"] == pytest.approx(
        mp3_duration_seconds(audio), abs=0.01
    )


def test_streaming_tts_collects_and_cancels() -> None:
    from canaisdark.integrations.elevenlabs import GenerationCancelled

    seen = {"url": ""}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, content=b"A" * 50_000)

    c, _ = client(httpx.MockTransport(handler))
    assert (
        c.text_to_speech(
            "v",
            "t",
            model_id="m",
            stability=0.5,
            similarity=0.5,
            speed=1,
            should_cancel=lambda: False,
        )
        == b"A" * 50_000
    )
    assert "/v1/text-to-speech/v/stream" in seen["url"]
    with pytest.raises(GenerationCancelled):
        c.text_to_speech(
            "v",
            "t",
            model_id="m",
            stability=0.5,
            similarity=0.5,
            speed=1,
            should_cancel=lambda: True,
        )  # cancela antes de enviar


def test_balance_does_not_block_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from canaisdark.config import get_settings
    from canaisdark.integrations.elevenlabs import Subscription
    from canaisdark.services import credits

    release = threading.Event()

    class Slow:
        def subscription(self) -> Subscription:
            release.wait(5)
            return Subscription(1, 10, tier="free")

    monkeypatch.setattr(get_settings(), "elevenlabs_fake", False)
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "k")
    credits.set_client_factory(lambda: Slow())  # type: ignore[arg-type,return-value]
    try:
        assert credits.get_balance(wait=False) is None  # não esperou a rede
        release.set()
        for _ in range(50):
            got = credits.get_balance(wait=False)
            if got:
                break
            threading.Event().wait(0.05)
        assert got and got.limit == 10
    finally:
        credits.set_client_factory(None)
