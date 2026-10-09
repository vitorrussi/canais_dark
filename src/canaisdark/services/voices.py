"""Escolha de voz e modelo: catálogo de modelos, vozes da conta e da biblioteca (com cache curto)."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import ElevenLabsLike, default_client

CACHE_SECONDS = 300.0
SAMPLE_MAX_CHARS = 150

_factory: Callable[[], ElevenLabsLike] = default_client
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    multiplier: float
    note: str


MODELS: list[ModelInfo] = [
    ModelInfo(
        "eleven_flash_v2_5",
        "Flash v2.5",
        0.5,
        "Metade do custo e muito rápido. Bom para testes e rascunhos; suporta português.",
    ),
    ModelInfo(
        "eleven_turbo_v2_5",
        "Turbo v2.5",
        0.5,
        "Meio-termo: custo de Flash com qualidade um pouco melhor.",
    ),
    ModelInfo(
        "eleven_multilingual_v2",
        "Multilingual v2",
        1.0,
        "Melhor qualidade e emoção para narração final (1 crédito por caractere).",
    ),
]


def model_multiplier(model_id: str) -> float:
    return next(
        (m.multiplier for m in MODELS if m.id == model_id),
        0.5 if ("flash" in model_id or "turbo" in model_id) else 1.0,
    )


def set_client_factory(factory: Callable[[], ElevenLabsLike] | None) -> None:
    """Para testes."""
    global _factory
    _factory = factory or default_client
    _cache.clear()


def _cached(
    key: str, loader: Callable[[], list[dict[str, Any]]], force: bool = False
) -> list[dict[str, Any]]:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and not force and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def account_voices(force: bool = False) -> list[dict[str, Any]]:
    if not get_settings().elevenlabs_ready:
        return []
    return _cached("account", lambda: _factory().list_voices(), force)


def library_voices(
    language: str = "pt",
    accent: str = "brazilian",
    gender: str = "",
    search: str = "",
    force: bool = False,
) -> list[dict[str, Any]]:
    if not get_settings().elevenlabs_ready:
        return []
    key = f"lib|{language}|{accent}|{gender}|{search}"
    return _cached(key, lambda: _factory().search_library(language, accent, gender, search), force)


def library_usable(tier: str) -> bool:
    """Vozes da biblioteca só funcionam via API em plano pago (e depois de adicionadas à conta)."""
    return bool(tier) and tier != "free"
