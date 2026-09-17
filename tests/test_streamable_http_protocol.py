"""Protocol tests using the same Streamable HTTP client flow as Open WebUI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.test_schema import EXPECTED_TOOL_SPECS, advertised_specs_from_list_tools

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-limit:]


@dataclass
class RunningServer:
    url: str
    process: subprocess.Popen[str]


@contextmanager
def running_mcp_server(
    *,
    server_args: tuple[str, ...] = ("openwebui_mcp_mock.py",),
    **extra_env: str,
) -> Iterator[RunningServer]:
    port = _free_port()
    stderr_path = Path(tempfile.mkstemp(prefix="mcp-stderr-", suffix=".log")[1])
    env = {
        **os.environ,
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        **extra_env,
    }
    python = env.get("MCP_SERVER_PYTHON", sys.executable)
    with stderr_path.open("w", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            [python, *server_args],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            text=True,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError(
                        f"MCP server exited {process.returncode}: {_read_tail(stderr_path)}"
                    )
                try:
                    # A malformed request is enough to prove the HTTP listener is ready.
                    httpx.post(url, timeout=0.2, content=b"{}")
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                process.terminate()
                process.wait(timeout=5)
                raise RuntimeError(f"MCP server did not start: {_read_tail(stderr_path)}")
            yield RunningServer(url=url, process=process)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    stderr_path.unlink(missing_ok=True)


@pytest.fixture()
def mcp_url():
    with running_mcp_server() as server:
        yield server.url


async def _session(url: str, **client_kwargs):
    async with streamablehttp_client(url, **client_kwargs) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


@pytest.mark.integration
async def test_initialize_list_and_call_via_streamable_http(mcp_url: str):
    """Verify the exact operation order used by Open WebUI's MCP client."""
    async for session in _session(mcp_url):
        tools = await session.list_tools()
        assert advertised_specs_from_list_tools(tools.tools) == EXPECTED_TOOL_SPECS

        result = await session.call_tool("find_municipalities", {"query": "Yokohama"})
        assert not result.isError
        assert "Yokohama" in result.content[0].text


@pytest.mark.integration
async def test_package_module_advertises_the_same_tool_specs_over_http():
    with running_mcp_server(server_args=("-m", "mcp_toolcall_lab")) as server:
        async for session in _session(server.url):
            tools = await session.list_tools()
            assert advertised_specs_from_list_tools(tools.tools) == EXPECTED_TOOL_SPECS


@pytest.mark.integration
async def test_unknown_tool_is_error(mcp_url: str):
    async for session in _session(mcp_url):
        result = await session.call_tool("find_transaction_price", {"query": "Yokohama"})
        assert result.isError
        assert "find_transaction_price" in result.content[0].text


@pytest.mark.integration
async def test_missing_required_argument_is_error(mcp_url: str):
    async for session in _session(mcp_url):
        result = await session.call_tool("find_municipalities", {})
        assert result.isError
        assert "query" in result.content[0].text.lower() or "missing" in result.content[0].text.lower()


@pytest.mark.integration
async def test_type_mismatch_is_error(mcp_url: str):
    async for session in _session(mcp_url):
        result = await session.call_tool(
            "find_transaction_prices",
            {"municipality_code": 14109, "year": 2025},
        )
        assert result.isError
        assert "string" in result.content[0].text.lower() or "municipality_code" in result.content[0].text


@pytest.mark.integration
async def test_empty_result_is_success_with_no_rows(mcp_url: str):
    async for session in _session(mcp_url):
        result = await session.call_tool("find_stations", {"municipality_code": "00000"})
        assert not result.isError
        assert result.content == []
        structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
        if structured is not None:
            assert structured.get("result") == []


@pytest.mark.integration
async def test_timeout_raises_on_slow_tool():
    with running_mcp_server(MCP_TOOL_DELAY_SECONDS="30") as server:
        async with streamablehttp_client(server.url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                task = asyncio.create_task(
                    session.call_tool("find_stations", {"municipality_code": "14109"})
                )
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                finally:
                    task.cancel()
                    server.process.terminate()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.wait_for(task, timeout=2)


@pytest.mark.integration
async def test_jsonl_log_records_success_empty_and_error():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "toolcalls.jsonl")
        with running_mcp_server(MCP_TOOLCALL_LOG=log_path) as server:
            async for session in _session(server.url):
                ok = await session.call_tool("find_municipalities", {"query": "Yokohama"})
                assert not ok.isError
                empty = await session.call_tool("find_stations", {"municipality_code": "00000"})
                assert not empty.isError
                err = await session.call_tool("find_transaction_price", {})
                assert err.isError

        events = [json.loads(line) for line in Path(log_path).read_text(encoding="utf-8").splitlines()]
        outcomes = {event["outcome"] for event in events}
        assert {"success", "empty", "error"} <= outcomes
        error_event = next(event for event in events if event["outcome"] == "error")
        assert error_event["tool"] == "find_transaction_price"
        assert "arguments" in error_event
