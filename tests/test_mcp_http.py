"""Stdlib MCP session follows the product handshake: initialize → tools/list → tools/call."""

from __future__ import annotations

import json

from mcp_toolcall_lab.mcp_http import McpStdlibSession


def test_stdlib_session_sends_tools_list_on_the_wire() -> None:
    posted: list[dict] = []

    class _Session(McpStdlibSession):
        def post(self, payload: dict, extra_headers: dict | None = None) -> str:
            posted.append(payload)
            return json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": []}})

    session = _Session("http://mcp-mock:8000/mcp")
    session.list_tools()
    assert posted[0]["method"] == "tools/list"
    assert posted[0]["jsonrpc"] == "2.0"
