"""Saldo de créditos do ElevenLabs (com cache curto para não chamar a API a cada página)."""

import threading
import time
from collections.abc import Callable

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import ElevenLabsLike, Subscription, default_client

_CACHE_SECONDS = 60.0
_FAIL_CACHE_SECONDS = (
    300.0  # falha: não insiste a cada página (cada tentativa pode custar segundos)
)
_cache: tuple[float, Subscription | None] | None = None
_refreshing = False
_factory: Callable[[], ElevenLabsLike] = default_client


def set_client_factory(factory: Callable[[], ElevenLabsLike] | None) -> None:
    """Para testes: substitui o cliente usado para consultar saldo."""
    global _factory, _cache
    _factory = factory or default_client
    _cache = None


def _fetch() -> Subscription | None:
    try:
        return _factory().subscription()
    except Exception:  # noqa: BLE001 - o saldo é opcional; nunca quebra a página
        return None


def _refresh_in_background() -> None:
    global _cache, _refreshing
    try:
        _cache = (time.monotonic(), _fetch())
    finally:
        _refreshing = False


def get_balance(force: bool = False, wait: bool = True) -> Subscription | None:
    """None se não há chave, se a chave não pode ler a conta ou se a API falhar.

    `wait=False` (páginas): nunca espera a rede — devolve o último valor conhecido e atualiza em segundo plano
    (o DNS/rede do servidor pode levar segundos). `force=True` (antes de gastar créditos): consulta e espera."""
    global _cache, _refreshing
    if not get_settings().elevenlabs_ready:
        return None
    now = time.monotonic()
    fresh = _cache is not None and now - _cache[0] < (
        _CACHE_SECONDS if _cache[1] else _FAIL_CACHE_SECONDS
    )
    if not force and fresh:
        return _cache[1] if _cache else None
    if wait or force:
        _cache = (now, _fetch())
        return _cache[1]
    if not _refreshing:
        _refreshing = True
        threading.Thread(target=_refresh_in_background, daemon=True).start()
    return _cache[1] if _cache else None
