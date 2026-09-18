"""Drive the MCP Streamable HTTP protocol with a real browser's ``fetch()``.

This mock server has **no chat screen** — `GET /` is a plain 404 and `/mcp`
only speaks JSON-RPC, so there is nothing here resembling Open WebUI's actual
UI to click through or screenshot. What *is* worth proving with a real
browser is the "HTTP APIで疎結合" (loosely coupled over plain HTTP) claim
from one more angle: not curl (a shell process), not the `mcp` SDK or
`gradio_client`-style Python client, but an actual browser's JavaScript
fetch(), the same primitive any real chat UI's frontend code would use.

No clicking, no page content assertions — this is `page.evaluate()` running
``fetch()`` from a same-origin page (navigated to the server's own 404 root
so the browser treats it as same-origin; cross-origin would hit CORS, since
this server sends no `Access-Control-Allow-Origin` header and 405s on
OPTIONS preflight — confirmed by hand, not asserted here since it isn't the
point of this module).
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server

pytestmark = pytest.mark.integration

_INIT_JS = """
async (clientInfo) => {
  const res = await fetch('/mcp', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'},
    body: JSON.stringify({
      jsonrpc: '2.0', id: 1, method: 'initialize',
      params: {protocolVersion: '2025-06-18', capabilities: {}, clientInfo},
    }),
  });
  window.__mcpSessionId = res.headers.get('mcp-session-id');
  await fetch('/mcp', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
      'Mcp-Session-Id': window.__mcpSessionId,
    },
    body: JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}),
  });
  return window.__mcpSessionId;
}
"""

_CALL_JS = """
async ({method, params}) => {
  const payload = {jsonrpc: '2.0', id: Date.now(), method};
  if (params !== null) payload.params = params;
  const res = await fetch('/mcp', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
      'Mcp-Session-Id': window.__mcpSessionId,
    },
    body: JSON.stringify(payload),
  });
  const text = await res.text();
  const match = text.match(/^data: (.*)$/m);
  return JSON.parse(match[1]).result;
}
"""


@pytest.fixture(scope="module")
def browser():
    executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    with sync_playwright() as p:
        launched = p.chromium.launch(executable_path=executable_path)
        yield launched
        launched.close()


@pytest.fixture()
def mcp_page(browser):
    with running_mcp_server() as server:
        origin = urlsplit(server.url)
        page = browser.new_page()
        # Same-origin as the MCP server (even though this 404s) so fetch()
        # below isn't cross-origin — see the module docstring on CORS.
        page.goto(f"{origin.scheme}://{origin.netloc}/", wait_until="load")
        page.evaluate(_INIT_JS, {"name": "playwright-browser", "version": "0.0.1"})
        try:
            yield page
        finally:
            page.close()


def _call(page, method: str, params: dict | None = None):
    return page.evaluate(_CALL_JS, {"method": method, "params": params})


def test_browser_fetch_tools_list_matches_expected_specs(mcp_page) -> None:
    tools = sorted(_call(mcp_page, "tools/list")["tools"], key=lambda t: t["name"])
    specs = [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in tools
    ]
    assert specs == EXPECTED_TOOL_SPECS


def test_browser_fetch_tool_call_success(mcp_page) -> None:
    result = _call(
        mcp_page, "tools/call", {"name": "find_municipalities", "arguments": {"query": "Yokohama"}}
    )
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == [
        {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}
    ]


def test_browser_fetch_empty_result(mcp_page) -> None:
    result = _call(
        mcp_page, "tools/call", {"name": "find_stations", "arguments": {"municipality_code": "00000"}}
    )
    assert result["isError"] is False
    assert result["content"] == []


def test_browser_fetch_unknown_tool_is_error(mcp_page) -> None:
    result = _call(
        mcp_page, "tools/call", {"name": "find_transaction_price", "arguments": {"query": "Yokohama"}}
    )
    assert result["isError"] is True
    assert "find_transaction_price" in result["content"][0]["text"]
