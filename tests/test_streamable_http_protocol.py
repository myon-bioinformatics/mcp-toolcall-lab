"""Protocol test using the same Streamable HTTP client flow as Open WebUI."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def mcp_url():
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "openwebui_mcp_mock.py"],
        cwd=ROOT,
        env={**os.environ, "MCP_HOST": "127.0.0.1", "MCP_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(50):
            try:
                # A malformed request is enough to prove the HTTP listener is ready.
                httpx.post(url, timeout=0.2, content=b"{}")
                break
            except httpx.ConnectError:
                time.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"MCP server did not start: {stderr}")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.integration
async def test_initialize_list_and_call_via_streamable_http(mcp_url: str):
    """Verify the exact operation order used by Open WebUI's MCP client."""
    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "find_municipalities" in names

            result = await session.call_tool("find_municipalities", {"query": "Yokohama"})
            assert not result.isError
            assert "Yokohama" in result.content[0].text
