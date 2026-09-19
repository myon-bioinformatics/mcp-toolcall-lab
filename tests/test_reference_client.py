"""Browserless coverage of the shared Gradio/Streamlit MCP layer.

Boots this repo's own tiny mock server the same way every other integration
test here does (``running_mcp_server``) — not a real chat product, so this
still belongs in the normal-PR lane per issue #14 ("no requirement to boot
every real chat product on every PR").
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mcp_toolcall_lab.reference_client import call_tool_for_prompt, discover_tools
from tests.test_streamable_http_protocol import mcp_url, running_mcp_server  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration


async def test_discover_tools_matches_advertised_names(mcp_url: str):
    tools = await discover_tools(mcp_url)
    assert {tool.name for tool in tools} == {
        "find_municipalities",
        "find_transaction_prices",
        "find_stations",
    }


async def test_call_tool_for_prompt_success(mcp_url: str):
    case = await call_tool_for_prompt(
        mcp_url,
        client="gradio",
        prompt="where is Yokohama",
        tool_name="find_municipalities",
        arguments={"query": "Yokohama"},
    )
    assert case.tool_called is True
    assert case.mcp_outcome == "success"
    assert case.assistant_received_result is True
    assert "Yokohama" in (case.result_text or "")


async def test_call_tool_for_prompt_empty(mcp_url: str):
    case = await call_tool_for_prompt(
        mcp_url,
        client="streamlit",
        prompt="stations near nowhere",
        tool_name="find_stations",
        arguments={"municipality_code": "00000"},
    )
    assert case.mcp_outcome == "empty"


async def test_call_tool_for_prompt_error_on_unknown_tool(mcp_url: str):
    case = await call_tool_for_prompt(
        mcp_url,
        client="gradio",
        prompt="find the transaction price",
        tool_name="find_transaction_price",  # missing the trailing "s"
        arguments={"municipality_code": "14109", "year": 2025},
    )
    assert case.mcp_outcome == "error"


async def test_call_tool_for_prompt_records_case_id_in_the_shared_log():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            case = await call_tool_for_prompt(
                server.url,
                client="gradio",
                prompt="where is Matsudo",
                tool_name="find_municipalities",
                arguments={"query": "Matsudo"},
            )

        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert len(events) == 1
        assert events[0]["meta"] == {
            "case_id": case.case_id,
            "client": "gradio",
            "source": "reference",
        }
