"""Stdlib Streamable HTTP client for the mock.

Not part of ``INLINE_MODULES``: this is a *caller* of ``/mcp``, the same
handshake ``tests/test_curl_protocol.py`` demonstrates with curl. The stub
front uses this so it does not grow a second copy of initialize / tools/list
/ SSE parse.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

ACCEPT = "application/json, text/event-stream"


def extract_sse_data(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no SSE/JSON body: {text[:200]!r}") from exc


class McpStdlibSession:
    """urllib-only Streamable HTTP session.

    Open WebUI and LibreChat both do ``initialize`` (then
    ``notifications/initialized``) → ``tools/list`` → ``tools/call``.
    This client follows that product handshake rather than skipping list.
    """

    def __init__(self, url: str, *, timeout: float = 15.0, client_name: str = "mcp-toolcall-lab") -> None:
        self.url = url
        self.timeout = timeout
        self.client_name = client_name
        self.session_id: str | None = None
        self._next_id = 1

    def post(
        self,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if extra_headers:
            headers.update(extra_headers)
        request = Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            session = response.headers.get("mcp-session-id")
            if session:
                self.session_id = session
            return response.read().decode("utf-8")

    def initialize(self) -> dict[str, Any]:
        raw = self.post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": "0.0.1"},
                },
            }
        )
        self._next_id += 1
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return extract_sse_data(raw)

    def list_tools(self) -> dict[str, Any]:
        raw = self.post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/list", "params": {}}
        )
        self._next_id += 1
        return extract_sse_data(raw)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if meta:
            params["_meta"] = meta
        raw = self.post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call", "params": params},
            extra_headers=extra_headers,
        )
        self._next_id += 1
        return extract_sse_data(raw)
