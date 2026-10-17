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
import pytest
import uvicorn
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamablehttp_client
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


async def call(url, path):
    kwargs = {"tool_name": "find_municipalities", "arguments": {"query": "Yokohama"}}
    if path == "chat":
        trace = await send_via_chat(url, user_text="Where is Yokohama?", **kwargs)
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
        assert not result.isError
        assert "Yokohama" in result.content[0].text


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
            assert errors and all(isinstance(error, httpx.HTTPStatusError) for error in errors)
            assert all(error.response.status_code == 307 for error in errors)
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
        async with streamablehttp_client(origin + "/mcp") as (read, write, session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                original = session_id()
                assert original
                await session.list_tools()
                await asyncio.sleep(IDLE_TIMEOUT * 3)
                result = await session.call_tool("find_municipalities", {"query": "Yokohama"})
                assert not result.isError
                assert session_id() == original


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
        assert not result.isError
        assert "Yokohama" in result.content[0].text


async def test_deleted_sdk_session_surfaces_error_then_new_connection_succeeds():
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        async with streamablehttp_client(url) as (read, write, session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                old_id = session_id()
                assert old_id
                async with httpx.AsyncClient() as client:
                    deleted = await client.delete(url, headers={"Mcp-Session-Id": old_id})
                    assert deleted.status_code == 200
                async with asyncio.timeout(5):
                    with pytest.raises(McpError, match="Session terminated"):
                        await session.call_tool("find_municipalities", {"query": "Yokohama"})
        result = await call(url, "direct")
        assert not result.isError
        assert "Yokohama" in result.content[0].text


async def test_session_capacity_error_preserves_existing_connection(short_idle_sessions):
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        short_idle_sessions[0].max_sessions = 1
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async with asyncio.timeout(5):
                    with pytest.raises(ExceptionGroup) as caught:
                        await call(url, "chat")
                errors = leaves(caught.value)
                assert errors and all(isinstance(error, httpx.HTTPStatusError) for error in errors)
                assert all(error.response.status_code == 503 for error in errors)
                assert (await session.list_tools()).tools
        # Closing the existing SDK transport sends DELETE and frees capacity.
        assert not (await call(url, "direct")).isError
