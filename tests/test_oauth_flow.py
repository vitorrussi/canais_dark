import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import Session

from canaisdark.main import create_app
from canaisdark.models import User
from canaisdark.services import oauth, users

pytestmark = pytest.mark.anyio

BASE = "http://localhost:8123"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
VERIFIER = "v" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)


@pytest.fixture
def app(session: Session) -> FastAPI:
    return create_app()


async def _register(c: httpx.AsyncClient) -> str:
    r = await c.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["client_id"])


async def _authorize(c: httpx.AsyncClient, client_id: str, **over: str) -> httpx.Response:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "xyz",
        **over,
    }
    return await c.get("/authorize", params=params)


async def _login_and_allow(c: httpx.AsyncClient, token: str, consent_url: str) -> str:
    """Segue o fluxo do navegador: consentimento → login (volta) → permitir → devolve o code."""
    r = await c.get(consent_url)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = await c.post("/login", data={"token": token})
    assert r.status_code == 303 and r.headers["location"].startswith("/oauth/consent?req=")
    page = await c.get(r.headers["location"])
    assert page.status_code == 200 and "Permitir" in page.text and "Claude" in page.text
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    r = await c.post("/oauth/consent", data={"req": req, "decision": "allow"})
    assert r.status_code == 303
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == REDIRECT
    q = parse_qs(loc.query)
    assert q["state"] == ["xyz"]
    return q["code"][0]


async def _token(c: httpx.AsyncClient, client_id: str, **data: str) -> httpx.Response:
    return await c.post("/token", data={"client_id": client_id, **data})


async def _full_flow(
    c: httpx.AsyncClient, token: str
) -> tuple[str, dict[str, Any]]:  # client_id, token json
    client_id = await _register(c)
    r = await _authorize(c, client_id)
    assert r.status_code == 302, r.text
    code = await _login_and_allow(c, token, r.headers["location"])
    r = await _token(
        c,
        client_id,
        grant_type="authorization_code",
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=VERIFIER,
    )
    assert r.status_code == 200, r.text
    return client_id, r.json()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE, follow_redirects=False
    )


async def test_discovery_and_challenge(app: FastAPI) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert meta["registration_endpoint"].endswith("/register")
        assert meta["code_challenge_methods_supported"] == ["S256"]
        res = await c.get("/.well-known/oauth-protected-resource/mcp")
        assert res.status_code == 200 and res.json()["resource"].endswith("/mcp")
        denied = await c.post("/mcp", json=INIT, headers=H)
        assert denied.status_code == 401
        assert "resource_metadata=" in denied.headers["www-authenticate"]


async def test_full_flow_then_mcp_refresh_and_revoke(
    app: FastAPI, user_token: tuple[User, str], session: Session
) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        client_id, tok = await _full_flow(c, user_token[1])
        assert tok["token_type"].lower() == "bearer" and tok["refresh_token"]

        ok = await c.post(
            "/mcp", json=INIT, headers={**H, "Authorization": f"Bearer {tok['access_token']}"}
        )
        assert ok.status_code == 200

        # o token de API (Claude Code) continua valendo
        legacy = await c.post(
            "/mcp", json=INIT, headers={**H, "Authorization": f"Bearer {user_token[1]}"}
        )
        assert legacy.status_code == 200

        # refresh rotaciona: o antigo para de funcionar
        r = await _token(
            c, client_id, grant_type="refresh_token", refresh_token=tok["refresh_token"]
        )
        assert r.status_code == 200, r.text
        new = r.json()
        assert new["refresh_token"] != tok["refresh_token"]
        again = await _token(
            c, client_id, grant_type="refresh_token", refresh_token=tok["refresh_token"]
        )
        assert again.status_code == 400

        grants = oauth.list_grants(session, user_token[0].id or 0)
        assert len(grants) == 1 and grants[0].client_name == "Claude"

        # revogar o conector invalida access e refresh
        assert oauth.revoke_grant(session, user_token[0].id or 0, grants[0].grant_id)
        r = await c.post(
            "/mcp", json=INIT, headers={**H, "Authorization": f"Bearer {new['access_token']}"}
        )
        assert r.status_code == 401
        r = await _token(
            c, client_id, grant_type="refresh_token", refresh_token=new["refresh_token"]
        )
        assert r.status_code == 400


async def test_code_is_single_use_and_pkce_enforced(
    app: FastAPI, user_token: tuple[User, str]
) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        client_id = await _register(c)
        r = await _authorize(c, client_id)
        code = await _login_and_allow(c, user_token[1], r.headers["location"])
        base = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
        }
        wrong = await _token(c, client_id, **base, code_verifier="x" * 64)
        assert wrong.status_code == 400
        good = await _token(c, client_id, **base, code_verifier=VERIFIER)
        assert good.status_code == 200
        reuse = await _token(c, client_id, **base, code_verifier=VERIFIER)
        assert reuse.status_code == 400


async def test_unregistered_redirect_is_rejected(app: FastAPI) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        client_id = await _register(c)
        r = await _authorize(c, client_id, redirect_uri="https://evil.example/cb")
        assert r.status_code == 400


async def test_deny_redirects_with_access_denied(
    app: FastAPI, user_token: tuple[User, str]
) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        client_id = await _register(c)
        r = await _authorize(c, client_id)
        await c.get(r.headers["location"])
        login = await c.post("/login", data={"token": user_token[1]})
        req = parse_qs(urlparse(login.headers["location"]).query)["req"][0]
        r = await c.post("/oauth/consent", data={"req": req, "decision": "deny"})
        assert r.status_code == 303
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["error"] == ["access_denied"] and q["state"] == ["xyz"]
        # o pedido foi consumido
        again = await c.post("/oauth/consent", data={"req": req, "decision": "allow"})
        assert again.status_code == 410


async def test_rotating_user_token_and_deactivating_revoke_connectors(
    app: FastAPI, user_token: tuple[User, str], session: Session
) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        _, tok = await _full_flow(c, user_token[1])
        auth = {**H, "Authorization": f"Bearer {tok['access_token']}"}
        assert (await c.post("/mcp", json=INIT, headers=auth)).status_code == 200
        users.issue_token(session, user_token[0])
        assert (await c.post("/mcp", json=INIT, headers=auth)).status_code == 401


async def test_deactivated_user_loses_connector(
    app: FastAPI, user_token: tuple[User, str], session: Session
) -> None:
    async with app.router.lifespan_context(app), _client(app) as c:
        _, tok = await _full_flow(c, user_token[1])
        auth = {**H, "Authorization": f"Bearer {tok['access_token']}"}
        users.deactivate_user(session, user_token[0])
        assert (await c.post("/mcp", json=INIT, headers=auth)).status_code == 401
