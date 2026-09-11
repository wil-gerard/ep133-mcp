"""The server must launch over stdio and keep stdout clean.

A successful MCP handshake and tool round-trip through the real stdio client
is the proof: any stray byte on stdout would break the JSON-RPC stream and the
client would fail. Runs with or without hardware attached — device_info must
either return real info or a structured DeviceUnavailable, never raise.
"""

import json
import sys

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = StdioServerParameters(command=sys.executable, args=["-m", "ep133_mcp.server"])


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    return json.loads(text)


@pytest.mark.asyncio
async def test_handshake_lists_tools():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
    assert tools == {"device_info", "server_status"}


@pytest.mark.asyncio
async def test_server_status_never_opens_the_port():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = _payload(await session.call_tool("server_status"))
    assert status["session_open"] is False
    assert status["write_tools_available"] is False


@pytest.mark.asyncio
async def test_device_info_is_structured_with_or_without_hardware():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            info = _payload(await session.call_tool("device_info"))
    if "error" in info:
        assert info["error"] == "DeviceUnavailable"
        assert "next_step" in info
    else:
        assert info["product"] == "EP-133"
        assert info["sample_free_bytes"] <= info["sample_capacity_bytes"]
        assert 1 <= info["active_project"] <= 9
        assert "serial" not in info, "serial must be opt-in"
