"""Contrato das tools MCP: documentadas, testadas e sem efeitos proibidos pelas regras do projeto."""

import re
from pathlib import Path

import pytest

from canaisdark.mcp import server

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "docs" / "mcp-tools.md").read_text(encoding="utf-8")
# Nenhuma tool MCP envia a terceiros nem aprova (decisões humanas ficam no site/REST).
FORBIDDEN_NAME = re.compile(
    r"(youtube_upload|upload_to|upload_video|publish|send|drive_sync|sync_drive|approve)"
)


async def test_every_tool_is_documented_described_and_safe() -> None:
    tools = await server.mcp.list_tools()
    assert len(tools) >= 20
    undocumented = [t.name for t in tools if f"`{t.name}(" not in DOCS]
    assert not undocumented, f"faltam em docs/mcp-tools.md: {undocumented}"
    nodoc = [t.name for t in tools if len((t.description or "").strip()) < 30]
    assert not nodoc, f"tools sem descrição útil: {nodoc}"
    forbidden = [t.name for t in tools if FORBIDDEN_NAME.search(t.name)]
    assert not forbidden, f"tools que enviam/aprovam não são permitidas: {forbidden}"


async def test_every_tool_is_exercised_by_some_test() -> None:
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "tests").glob("test_*.py")
        if p.name != "test_mcp_contract.py"
    )
    tools = await server.mcp.list_tools()
    untested = [
        t.name for t in tools if not re.search(rf"[\"']{t.name}[\"']|\b{t.name}\(", sources)
    ]
    assert not untested, f"tools sem nenhum teste: {untested}"


def test_documented_tools_exist() -> None:
    """Documentação não pode listar tool que não existe mais."""
    import asyncio

    registered = {t.name for t in asyncio.run(server.mcp.list_tools())}
    documented = set(re.findall(r"^\| `([a-z_]+)\(", DOCS, flags=re.MULTILINE))
    assert documented, "não consegui ler a tabela de tools em docs/mcp-tools.md"
    assert documented <= registered, f"documentadas e inexistentes: {documented - registered}"
