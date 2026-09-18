"""Speak to the mock with FastMCP's own bundled CLI (`fastmcp list` / `fastmcp call`).

``fastmcp`` is already a pinned dependency of this project (see pyproject.toml),
so unlike curl (an extra tool assumed to be on the box) or Playwright (an extra
pip install), this CLI needs nothing beyond what ``pip install -e .`` already
puts on PATH — the most "native" way to smoke-test this server, the same way
``python -m playwright screenshot`` reaches for Playwright's own CLI instead of
a hand-written client. ``scripts/mcp_fastmcp_cli_smoke.sh`` demonstrates it as a
standalone one-liner-able tool; this module gives it pytest coverage,
complementing ``test_curl_protocol.py`` (raw HTTP) and
``test_streamable_http_protocol.py`` (the ``mcp`` SDK).

One real behavioral difference from the raw-protocol tests: the CLI resolves
tool names against its own `tools/list` result *before* calling, so an unknown
tool name never reaches the server as an ``isError: true`` ``tools/call`` — it
fails client-side with a non-zero exit instead. ``test_unknown_tool_fails_client_side``
covers that directly, in contrast to ``test_curl_unknown_tool_is_error`` and
``test_unknown_tool_is_error`` (both of which see the server's ``isError``).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server

FASTMCP = shutil.which("fastmcp")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(FASTMCP is None, reason="fastmcp CLI is not on PATH"),
]


def _run(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [FASTMCP, *args], capture_output=True, text=True, timeout=timeout
    )


def _list_tools(url: str) -> list[dict]:
    result = _run("list", url, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["tools"]


def _call(url: str, tool: str, **arguments: str) -> subprocess.CompletedProcess[str]:
    args = [f"{key}={value}" for key, value in arguments.items()]
    result = _run("call", url, tool, *args, "--json")
    return result


@pytest.fixture()
def mcp_url():
    with running_mcp_server() as server:
        yield server.url


def test_cli_list_matches_expected_specs(mcp_url: str) -> None:
    tools = sorted(_list_tools(mcp_url), key=lambda t: t["name"])
    specs = [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in tools
    ]
    assert specs == EXPECTED_TOOL_SPECS


def test_cli_call_success(mcp_url: str) -> None:
    result = _call(mcp_url, "find_municipalities", query="Yokohama")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["is_error"] is False
    assert payload["structured_content"]["result"] == [
        {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}
    ]


def test_cli_call_empty_result(mcp_url: str) -> None:
    result = _call(mcp_url, "find_stations", municipality_code="00000")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["is_error"] is False
    assert payload["content"] == []
    assert payload["structured_content"]["result"] == []


def test_unknown_tool_fails_client_side(mcp_url: str) -> None:
    """Unlike the raw-protocol tests, the CLI never sends this to the server."""
    result = _call(mcp_url, "find_transaction_price", query="Yokohama")
    assert result.returncode != 0
    assert "find_transaction_price" in result.stdout
