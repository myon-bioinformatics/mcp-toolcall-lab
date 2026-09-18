"""Speak the Streamable HTTP protocol with ``httpx`` — a plain Python HTTP client, no MCP SDK.

``test_curl_protocol.py`` proves the API is reachable from a shell, with no
Python at all. ``test_streamable_http_protocol.py`` proves it works through
the official ``mcp`` SDK. This module covers the gap between them: a minimal
Python integration (e.g. a lightweight chat backend that doesn't want the
full ``mcp`` SDK as a dependency) reaching for a plain HTTP client library
instead — ``httpx`` is already a pinned test dependency here (used so far
only to poll for server readiness in ``running_mcp_server()``), so this adds
no new dependency.

Two things this layer demonstrates that neither curl nor the SDK does as
directly:

- A single ``httpx.Client`` is reused (keep-alive) across every call in a
  session, the way a real long-lived service would talk to this server —
  unlike curl, which spawns a brand-new process (and connection) per call.
- Timing out is a plain HTTP client timeout (``httpx.TimeoutException``) on
  the raw request, not something that needs the ``mcp`` SDK's async
  cancellation machinery — see ``test_httpx_timeout_on_slow_tool`` versus
  ``test_timeout_raises_on_slow_tool`` in ``test_streamable_http_protocol.py``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server

pytestmark = pytest.mark.integration

ACCEPT = "application/json, text/event-stream"


def _extract_sse_data(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no SSE data line in response: {text!r}")


class McpHttpxSession:
    """A Streamable HTTP client built on one reused ``httpx.Client`` — no MCP SDK."""

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        self.url = url
        self.client = httpx.Client(timeout=timeout)
        self.session_id: str | None = None
        self._next_id = 1

    def close(self) -> None:
        self.client.close()

    def _post(self, payload: dict) -> httpx.Response:
        headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return self.client.post(self.url, headers=headers, json=payload)

    def initialize(self) -> dict:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "httpx-test", "version": "0.0.1"},
                },
            }
        )
        self._next_id += 1
        self.session_id = response.headers.get("mcp-session-id")
        assert self.session_id, f"no Mcp-Session-Id header in response: {response.headers!r}"

        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return _extract_sse_data(response.text)["result"]

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._next_id += 1
        headers = {"Content-Type": "application/json", "Accept": ACCEPT, "Mcp-Session-Id": self.session_id}
        response = self.client.post(self.url, headers=headers, json=payload, timeout=timeout)
        return _extract_sse_data(response.text)["result"]

    def list_tools(self) -> list[dict]:
        return self.call("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        return self.call("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)


@pytest.fixture()
def httpx_session():
    with running_mcp_server() as server:
        session = McpHttpxSession(server.url)
        session.initialize()
        try:
            yield session
        finally:
            session.close()


def test_httpx_tools_list_matches_expected_specs(httpx_session: McpHttpxSession) -> None:
    tools = sorted(httpx_session.list_tools(), key=lambda t: t["name"])
    specs = [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in tools
    ]
    assert specs == EXPECTED_TOOL_SPECS


def test_httpx_tools_call_success(httpx_session: McpHttpxSession) -> None:
    result = httpx_session.call_tool("find_municipalities", {"query": "Yokohama"})
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == [
        {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}
    ]


def test_httpx_tools_call_empty_result(httpx_session: McpHttpxSession) -> None:
    result = httpx_session.call_tool("find_stations", {"municipality_code": "00000"})
    assert result["isError"] is False
    assert result["content"] == []
    assert result["structuredContent"]["result"] == []


def test_httpx_unknown_tool_is_error(httpx_session: McpHttpxSession) -> None:
    result = httpx_session.call_tool("find_transaction_price", {"query": "Yokohama"})
    assert result["isError"] is True
    assert "find_transaction_price" in result["content"][0]["text"]


def test_httpx_missing_required_argument_is_error(httpx_session: McpHttpxSession) -> None:
    result = httpx_session.call_tool("find_municipalities", {})
    assert result["isError"] is True
    text = result["content"][0]["text"].lower()
    assert "query" in text or "missing" in text


def test_httpx_tools_call_reuses_the_same_session_across_calls(httpx_session: McpHttpxSession) -> None:
    """The one httpx.Client from the fixture (and its Mcp-Session-Id) serves several calls in a row."""
    for _ in range(3):
        result = httpx_session.call_tool("find_municipalities", {"query": "Yokohama"})
        assert result["isError"] is False


def test_httpx_timeout_on_slow_tool() -> None:
    """A plain httpx timeout on the raw request — no mcp SDK cancellation involved."""
    with running_mcp_server(MCP_TOOL_DELAY_SECONDS="30") as server:
        session = McpHttpxSession(server.url)
        try:
            session.initialize()
            with pytest.raises(httpx.TimeoutException):
                session.call_tool("find_stations", {"municipality_code": "14109"}, timeout=0.5)
        finally:
            session.close()
