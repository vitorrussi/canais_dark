import httpx
import pytest
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from canaisdark.main import create_app
from canaisdark.models import User

pytestmark = pytest.mark.anyio

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


@pytest.fixture
def app(session: object) -> FastAPI:
    return create_app()


async def test_requires_bearer(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8123") as c:
            r = await c.post("/mcp", json=INIT, headers=H)
            assert r.status_code == 401
            r = await c.post("/mcp", json=INIT, headers={**H, "Authorization": "Bearer cd_x"})
            assert r.status_code == 401


async def test_authenticated_call_end_to_end(app: FastAPI, user_token: tuple[User, str]) -> None:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:8123",
            headers={"Authorization": f"Bearer {user_token[1]}"},
        ) as http:
            async with Client(
                streamable_http_client("http://localhost:8123/mcp", http_client=http)
            ) as client:
                result = await client.call_tool("list_channels", {})
                assert not result.is_error
