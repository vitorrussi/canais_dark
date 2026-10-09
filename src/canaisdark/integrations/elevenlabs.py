"""Cliente ElevenLabs (TTS, Forced Alignment, saldo) + fake para testes. Nunca loga a chave."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

BASE_URL = "https://api.elevenlabs.io"
MP3_BITRATE = 128_000  # mp3_44100_128: CBR, usado para estimar duração sem ffmpeg


class ElevenLabsError(Exception):
    """Erro com mensagem já em português para exibir no site."""

    def __init__(self, message: str, status: int = 0, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class Subscription:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class ElevenLabsLike(Protocol):
    def text_to_speech(
        self,
        voice_id: str,
        text: str,
        *,
        model_id: str,
        stability: float,
        similarity: float,
        speed: float,
        previous_text: str = "",
        next_text: str = "",
    ) -> bytes: ...

    def forced_alignment(self, audio: bytes, text: str) -> dict[str, Any]: ...

    def subscription(self) -> Subscription | None: ...


def credit_multiplier(model_id: str) -> float:
    """Flash/Turbo custam ~0,5 crédito por caractere; os demais, 1."""
    return 0.5 if ("flash" in model_id or "turbo" in model_id) else 1.0


def mp3_duration_seconds(audio: bytes) -> float:
    return len(audio) * 8 / MP3_BITRATE


def _message(resp: httpx.Response) -> tuple[str, str]:
    """(code, mensagem) do corpo de erro da API."""
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return "", resp.text[:200]
    if isinstance(detail, dict):
        return str(detail.get("status", "")), str(detail.get("message", ""))
    if isinstance(detail, list) and detail:
        return "validation", str(detail[0].get("msg", ""))
    return "", str(detail or "")[:200]


def map_error(resp: httpx.Response) -> ElevenLabsError:
    code, msg = _message(resp)
    s = resp.status_code
    low = f"{code} {msg}".lower()
    if "quota" in low or "exceeds your quota" in low:
        return ElevenLabsError(f"Créditos do ElevenLabs insuficientes: {msg}", s)
    if "unusual activity" in low or "free tier usage disabled" in low:
        return ElevenLabsError(
            "Uso gratuito bloqueado pelo ElevenLabs (atividade incomum — VPN/proxy?). Veja a conta no site deles.",
            s,
        )
    if s == 401:
        return ElevenLabsError(
            "Chave do ElevenLabs inválida ou sem permissão (ELEVENLABS_API_KEY).", s
        )
    if s in (402, 403) or "paid" in low or "not available for free" in low:
        return ElevenLabsError(
            f"Recurso/voz indisponível no seu plano. Use uma voz premade. Detalhe: {msg}", s
        )
    if s == 404 or "voice_not_found" in low:
        return ElevenLabsError("Voz não encontrada: confira o voice_id do canal.", s)
    if s == 422:
        return ElevenLabsError(f"Requisição recusada pelo ElevenLabs: {msg}", s)
    if s == 429:
        return ElevenLabsError(
            "Muitas requisições simultâneas ao ElevenLabs; tentando de novo.", s, retryable=True
        )
    if s >= 500:
        return ElevenLabsError(f"O ElevenLabs está instável (HTTP {s}).", s, retryable=True)
    return ElevenLabsError(f"Erro do ElevenLabs (HTTP {s}): {msg}", s)


class ElevenLabsClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        timeout: float = 180.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY não configurada no .env")
        self._http = httpx.Client(
            base_url=base_url, headers={"xi-api-key": api_key}, timeout=timeout, transport=transport
        )
        self._sleep = sleep

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        last: ElevenLabsError | None = None
        for attempt in range(3):
            try:
                resp = self._http.request(method, url, **kw)
            except httpx.TimeoutException as e:
                last = ElevenLabsError("Tempo esgotado ao falar com o ElevenLabs.", retryable=True)
                last.__cause__ = e
            except httpx.HTTPError as e:
                last = ElevenLabsError("Falha de rede ao falar com o ElevenLabs.", retryable=True)
                last.__cause__ = e
            else:
                if resp.status_code < 400:
                    return resp
                last = map_error(resp)
            if not last.retryable:
                raise last
            self._sleep(2.0 * (attempt + 1))
        assert last is not None
        raise last

    def text_to_speech(
        self,
        voice_id: str,
        text: str,
        *,
        model_id: str,
        stability: float,
        similarity: float,
        speed: float,
        previous_text: str = "",
        next_text: str = "",
    ) -> bytes:
        body: dict[str, Any] = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity,
                "speed": speed,
            },
        }
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        resp = self._request(
            "POST",
            f"/v1/text-to-speech/{voice_id}",
            params={"output_format": "mp3_44100_128"},
            json=body,
        )
        return resp.content

    def forced_alignment(self, audio: bytes, text: str) -> dict[str, Any]:
        resp = self._request(
            "POST",
            "/v1/forced-alignment",
            files={"file": ("narration.mp3", audio, "audio/mpeg")},
            data={"text": text},
        )
        data: dict[str, Any] = resp.json()
        return data

    def subscription(self) -> Subscription | None:
        """Saldo de créditos; None se a chave não tiver permissão de leitura da conta."""
        try:
            resp = self._request("GET", "/v1/user/subscription")
            d = resp.json()
            return Subscription(used=int(d["character_count"]), limit=int(d["character_limit"]))
        except (ElevenLabsError, KeyError, ValueError):
            return None


class FakeElevenLabs:
    """Sem rede e sem créditos: áudio sintético (CBR 128 kbps) e alinhamento proporcional."""

    CHARS_PER_SECOND = 15.0

    def __init__(self, credits: int = 10_000, high_loss_words: set[str] | None = None) -> None:
        self.used = 0
        self.limit = credits
        self.high_loss_words = high_loss_words or set()
        self.tts_calls: list[dict[str, Any]] = []
        self.alignment_calls = 0
        self.fail_on_call: int | None = None  # nº (1-based) da chamada de TTS que deve falhar
        self.alignment_error: ElevenLabsError | None = None

    def text_to_speech(self, voice_id: str, text: str, **kw: Any) -> bytes:
        self.tts_calls.append({"voice_id": voice_id, "text": text, **kw})
        if self.fail_on_call == len(self.tts_calls):
            raise ElevenLabsError("falha simulada", status=500)
        self.used += int(len(text) * credit_multiplier(kw.get("model_id", "")))
        seconds = max(0.5, len(text) / self.CHARS_PER_SECOND)
        return b"\xff\xfb" * int(seconds * MP3_BITRATE / 8 / 2)

    def forced_alignment(self, audio: bytes, text: str) -> dict[str, Any]:
        self.alignment_calls += 1
        if self.alignment_error:
            raise self.alignment_error
        total = mp3_duration_seconds(audio)
        tokens = text.split()
        weights = [max(1, len(t)) for t in tokens]
        scale = total / sum(weights) if weights else 0
        words, cursor = [], 0.0
        for tok, w in zip(tokens, weights, strict=True):
            end = cursor + w * scale
            loss = 0.9 if tok.strip(".,!?;:\"'").lower() in self.high_loss_words else 0.01
            words.append(
                {"text": tok, "start": round(cursor, 3), "end": round(end, 3), "loss": loss}
            )
            cursor = end
        return {"words": words, "characters": [], "loss": 0.02}

    def subscription(self) -> Subscription | None:
        return Subscription(self.used, self.limit)
