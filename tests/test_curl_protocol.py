"""Speak the Streamable HTTP protocol with nothing but ``curl``.

Open WebUI (and any other MCP client) talks to this server over plain HTTP:
POST a JSON-RPC request, read the ``Mcp-Session-Id`` response header, and
attach it to every following request. That handshake needs no MCP SDK — curl
does it directly, which is what ``scripts/mcp_curl_smoke.sh`` demonstrates as
a standalone one-liner-able tool. This module gives the same handshake pytest
coverage, complementing ``test_streamable_http_protocol.py``'s SDK-based
client flow with a raw-HTTP one that doesn't import ``mcp`` at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server

CURL = shutil.which("curl")
ACCEPT = "application/json, text/event-stream"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(CURL is None, reason="curl is not available on this system"),
]


def _extract_sse_data(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no SSE data line in response: {text!r}")


class McpCurlSession:
    """Minimal Streamable HTTP client built entirely out of ``curl`` calls."""

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 1

    def _post(self, payload: dict) -> subprocess.CompletedProcess[str]:
        headers = ["-H", "Content-Type: application/json", "-H", f"Accept: {ACCEPT}"]
        if self.session_id:
            headers += ["-H", f"Mcp-Session-Id: {self.session_id}"]
        return subprocess.run(
            [CURL, "-sS", "-i", "-X", "POST", self.url, *headers, "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=self.timeout, check=True,
        )

    def initialize(self) -> dict:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "curl-test", "version": "0.0.1"},
                },
            }
        )
        self._next_id += 1
        for line in result.stdout.splitlines():
            if line.lower().startswith("mcp-session-id:"):
                self.session_id = line.split(":", 1)[1].strip()
        assert self.session_id, f"no Mcp-Session-Id header in response: {result.stdout!r}"

        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return _extract_sse_data(result.stdout)["result"]

    def call(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._next_id += 1
        result = self._post(payload)
        return _extract_sse_data(result.stdout)["result"]

    def list_tools(self) -> list[dict]:
        return self.call("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.call("tools/call", {"name": name, "arguments": arguments})


@pytest.fixture()
def curl_session():
    with running_mcp_server() as server:
        session = McpCurlSession(server.url)
        session.initialize()
        yield session


def test_curl_tools_list_matches_expected_specs(curl_session: McpCurlSession) -> None:
    tools = sorted(curl_session.list_tools(), key=lambda t: t["name"])
    specs = [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in tools
    ]
    assert specs == EXPECTED_TOOL_SPECS


def test_curl_tools_call_success(curl_session: McpCurlSession) -> None:
    result = curl_session.call_tool("find_municipalities", {"query": "Yokohama"})
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == [
        {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}
    ]


def test_curl_tools_call_empty_result(curl_session: McpCurlSession) -> None:
    result = curl_session.call_tool("find_stations", {"municipality_code": "00000"})
    assert result["isError"] is False
    assert result["content"] == []
    assert result["structuredContent"]["result"] == []


def test_curl_unknown_tool_is_error(curl_session: McpCurlSession) -> None:
    result = curl_session.call_tool("find_transaction_price", {"query": "Yokohama"})
    assert result["isError"] is True
    assert "find_transaction_price" in result["content"][0]["text"]


def test_curl_missing_required_argument_is_error(curl_session: McpCurlSession) -> None:
    result = curl_session.call_tool("find_municipalities", {})
    assert result["isError"] is True
    text = result["content"][0]["text"].lower()
    assert "query" in text or "missing" in text
