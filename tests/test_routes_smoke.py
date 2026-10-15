"""Guarda de todas as rotas HTTP: nenhuma esquece autenticação e nenhuma devolve erro 500.

As rotas são descobertas sozinhas a partir do app; rota nova entra na verificação sem editar este
arquivo (a menos que seja pública: aí precisa entrar em PUBLIC com justificativa).
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from tests.media import setup_video

# Rotas que, por desenho, respondem sem sessão ou token (cada uma tem a sua própria validação).
PUBLIC = (
    r"^/health$",
    r"^/login$",
    r"^/privacy$",
    r"^/invite/",  # link de acesso assinado
    r"^/x/",  # links assinados da edição livre/thumbnail
    r"^/oauth/",  # consentimento OAuth (a própria tela exige login)
    r"^/\.well-known/",
    r"^/(register|authorize|token|revoke)$",  # OAuth do conector MCP
)
SKIP = (r"^/docs", r"^/openapi", r"^/redoc", r"^/static", r"^/mcp")
PARAM = re.compile(r"{([^}:]+)(?::[^}]*)?}")


def walk(routes: list[Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    for route in routes:
        kind = type(route).__name__
        if kind == "_IncludedRouter":
            yield from walk(
                list(route.original_router.routes), prefix + route.include_context.prefix
            )
        elif hasattr(route, "methods") and hasattr(route, "path"):
            for method in sorted(route.methods or []):
                if method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    yield method, prefix + route.path


def all_routes(app: FastAPI) -> list[tuple[str, str]]:
    found = sorted(set(walk(list(app.routes))))
    return [(m, p) for m, p in found if not any(re.search(s, p) for s in SKIP)]


def is_public(path: str) -> bool:
    return any(re.search(p, path) for p in PUBLIC)


@pytest.fixture
def world(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    ids = setup_video(session, user_token[0], [2.0, 3.0])
    return {"client": client, "ids": ids, "token": user_token[1]}


def fill(path: str, ids: dict[str, int]) -> str:
    known = {
        "video_id": ids["video_id"],
        "channel_id": ids["channel_id"],
        "scene_index": 0,
        "version": 1,
        "number": 1,
        "version_id": ids["version_id"],
        "script_id": ids["version_id"],
        "slug": "canal-teste",
        "signed": "assinatura-invalida",
        "format": "txt",
        "type": "generate_script",
        "task_type": "generate_script",
    }
    return PARAM.sub(lambda m: str(known.get(m.group(1), 999999)), path)


def test_every_route_requires_login_unless_public(world: dict[str, Any]) -> None:
    client: TestClient = world["client"]
    offenders = []
    for method, path in all_routes(client.app):  # type: ignore[arg-type]
        if is_public(path):
            continue
        url = fill(path, world["ids"])
        res = client.request(method, url, follow_redirects=False)
        if res.status_code not in (303, 401, 403):
            offenders.append(f"{method} {path} -> {res.status_code}")
    assert not offenders, "rotas sem proteção de login:\n" + "\n".join(offenders)


def test_no_route_returns_500_when_logged_in(world: dict[str, Any]) -> None:
    client: TestClient = world["client"]
    offenders = []
    routes = all_routes(client.app)  # type: ignore[arg-type]
    assert len(routes) > 100, "descoberta de rotas falhou (esperava mais de 100)"
    ordered = [r for r in routes if r[0] == "GET"] + [r for r in routes if r[0] != "GET"]
    for method, path in ordered:
        if path == "/logout" or path.startswith("/invite/"):
            continue  # encerram/alteram a sessão do próprio teste
        client.cookies.clear()
        client.post("/login", data={"token": world["token"]})
        url = fill(path, world["ids"])
        headers = {"Authorization": f"Bearer {world['token']}"} if path.startswith("/api") else {}
        try:
            res = client.request(method, url, headers=headers, follow_redirects=False)
        except Exception as e:  # o TestClient repassa exceções não tratadas do servidor
            offenders.append(f"{method} {path} -> exceção {type(e).__name__}: {str(e)[:120]}")
            continue
        if res.status_code >= 500:
            offenders.append(f"{method} {path} -> {res.status_code}")
    assert not offenders, "rotas com erro 500:\n" + "\n".join(offenders)


def test_public_list_is_not_stale(world: dict[str, Any]) -> None:
    """Cada padrão público deve corresponder a alguma rota real (evita lista morta)."""
    paths = [p for _, p in all_routes(world["client"].app)]
    dead = [pat for pat in PUBLIC if not any(re.search(pat, p) for p in paths)]
    assert not dead, f"padrões públicos sem rota correspondente: {dead}"
