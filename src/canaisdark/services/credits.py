"""Saldo de créditos do ElevenLabs (com cache curto para não chamar a API a cada página)."""

import time
from collections.abc import Callable

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import ElevenLabsLike, Subscription, default_client

_CACHE_SECONDS = 60.0
_cache: tuple[float, Subscription | None] | None = None
_factory: Callable[[], ElevenLabsLike] = default_client


def set_client_factory(factory: Callable[[], ElevenLabsLike] | None) -> None:
    """Para testes: substitui o cliente usado para consultar saldo."""
    global _factory, _cache
    _factory = factory or default_client
    _cache = None


def get_balance(force: bool = False) -> Subscription | None:
    """None se não há chave, se a chave não pode ler a conta ou se a API falhar."""
    global _cache
    if not get_settings().elevenlabs_ready:
        return None
    now = time.monotonic()
    if not force and _cache and now - _cache[0] < _CACHE_SECONDS:
        return _cache[1]
    try:
        value = _factory().subscription()
    except Exception:  # noqa: BLE001 - o saldo é opcional; nunca quebra a página
        value = None
    _cache = (now, value)
    return value
