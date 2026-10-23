"""Exercise SDK upgrade boundaries through the lab's real FastMCP HTTP app.

The independent ``fastmcp`` package still inherits the official SDK's HTTP
session manager. Short idle limits below are test-only; production defaults
and the chat/direct callers are left untouched.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
import uvicorn
from mcp import ClientSession, MCPError
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

import fastmcp.server.http as fastmcp_http
from mcp_toolcall_lab.chat_sim import send_direct, send_via_chat
from mcp_toolcall_lab.server import create_mcp

pytestmark = pytest.mark.integration
IDLE_TIMEOUT = 0.2


@asynccontextmanager
async def serve(app):
    """Bind a real loopback socket so open GET streams are not ASGI-buffered."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


def _recording_client() -> tuple[httpx2.AsyncClient, list[str]]:
    """Capture MCP session ids. The SDK client no longer yields a session getter."""
    seen: list[str] = []

    async def remember(response: httpx2.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            seen.append(session_id)

    return httpx2.AsyncClient(event_hooks={"response": [remember]}), seen


async def call(url, path):
    kwargs = {"tool_name": "find_municipalities", "arguments": {"query": "Exampleville"}}
    if path == "chat":
        trace = await send_via_chat(url, user_text="Where is Exampleville?", **kwargs)
        assert trace.tool_result_message["role"] == "tool"
        assert trace.tool_result_message["tool_call_id"] == trace.call_id
        return trace.mcp_result
    return await send_direct(url, **kwargs)


def leaves(exc):
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for child in exc.exceptions for leaf in leaves(child)]
    return [exc]


def redirect_app(location):
    async def redirect(request):
        return RedirectResponse(app.state.location, status_code=307)

    app = Starlette(routes=[Route("/redirect", redirect, methods=["POST", "GET", "DELETE"])])
    app.state.location = location
    return app


@pytest.mark.parametrize("path", ["chat", "direct"])
async def test_same_origin_redirect_preserves_tool_call(path):
    app = create_mcp().http_app(path="/mcp")
    app.router.routes.extend(redirect_app("/mcp").routes)
    async with serve(app) as origin:
        async with asyncio.timeout(5):
            result = await call(origin + "/redirect", path)
        assert not result.is_error
        assert "Exampleville" in result.content[0].text


@pytest.mark.parametrize("path", ["chat", "direct"])
@pytest.mark.parametrize("changed", ["host", "port"])
async def test_cross_origin_redirect_fails_without_contacting_target(path, changed):
    hits = []

    async def sink(request):
        hits.append(request.method)
        return Response(status_code=500)

    sink_app = Starlette(routes=[Route("/mcp", sink, methods=["POST", "GET", "DELETE"])])
    async with serve(sink_app) as target:
        app = redirect_app(target + "/mcp")
        async with serve(app) as origin:
            # For the host case keep the source port: only the hostname differs.
            if changed == "host":
                app.state.location = origin.replace("127.0.0.1", "localhost") + "/mcp"
                app.router.routes.append(Route("/mcp", sink, methods=["POST", "GET", "DELETE"]))
            async with asyncio.timeout(5):
                with pytest.raises(ExceptionGroup) as caught:
                    await call(origin + "/redirect", path)
            errors = leaves(caught.value)
            assert errors and all(isinstance(error, MCPError) for error in errors)
            assert all("not followed" in str(error) for error in errors)
        assert hits == []


@pytest.fixture
def short_idle_sessions(monkeypatch):
    """Use FastMCP's real inherited manager, accelerating only its idle clock."""
    manager_type = fastmcp_http.FastMCPStreamableHTTPSessionManager
    managers = []

    class ShortIdleManager(manager_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Verify this applies to our independent FastMCP server too.
            assert self.session_idle_timeout == 1800
            assert self.max_sessions == 10_000
            self.session_idle_timeout = IDLE_TIMEOUT
            managers.append(self)

    monkeypatch.setattr(fastmcp_http, "FastMCPStreamableHTTPSessionManager", ShortIdleManager)
    return managers


async def test_sdk_get_stream_keeps_session_alive(short_idle_sessions):
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        client, seen = _recording_client()
        async with client:
            async with streamable_http_client(origin + "/mcp", http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert seen
                    original = seen[-1]
                    await session.list_tools()
                    await asyncio.sleep(IDLE_TIMEOUT * 3)
                    result = await session.call_tool("find_municipalities", {"query": "Exampleville"})
                    assert not result.is_error
                    assert seen[-1] == original


async def test_idle_http_session_returns_404_then_fresh_chat_succeeds(short_idle_sessions):
    """POST-only clients can expire; a new chat turn initializes a fresh session."""
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/json, text/event-stream"}
            opened = await client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "idle-boundary", "version": "1"}},
            })
            opened.raise_for_status()
            old_id = opened.headers["mcp-session-id"]
            headers["Mcp-Session-Id"] = old_id
            initialized = await client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            assert initialized.status_code == 202
            await asyncio.sleep(IDLE_TIMEOUT * 3)
            expired = await client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            })
            assert expired.status_code == 404
        result = await call(url, "chat")
        assert not result.is_error
        assert "Exampleville" in result.content[0].text


async def test_deleted_sdk_session_surfaces_error_then_new_connection_succeeds():
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        http, seen = _recording_client()
        async with http:
            async with streamable_http_client(url, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert seen
                    old_id = seen[-1]
                    async with httpx.AsyncClient() as probe:
                        deleted = await probe.delete(url, headers={"Mcp-Session-Id": old_id})
                        assert deleted.status_code == 200
                    async with asyncio.timeout(5):
                        with pytest.raises(MCPError, match="Session not found"):
                            await session.call_tool("find_municipalities", {"query": "Exampleville"})
        result = await call(url, "direct")
        assert not result.is_error
        assert "Exampleville" in result.content[0].text


async def test_session_capacity_error_preserves_existing_connection(short_idle_sessions):
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        short_idle_sessions[0].max_sessions = 1
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async with asyncio.timeout(5):
                    with pytest.raises(ExceptionGroup) as caught:
                        await call(url, "chat")
                errors = leaves(caught.value)
                assert errors and all(isinstance(error, MCPError) for error in errors)
                assert all("Too many open sessions" in str(error) for error in errors)
                assert (await session.list_tools()).tools
        # Closing the existing SDK transport sends DELETE and frees capacity.
        assert not (await call(url, "direct")).is_error
