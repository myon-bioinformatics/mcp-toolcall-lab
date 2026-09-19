"""Browserless coverage of the shared Gradio/Streamlit MCP client layer.

No Gradio or Streamlit import here on purpose: this module tests
``mcp_toolcall_lab.reference_client`` directly, the same layer both UI
adapters call into, so these assertions hold regardless of which UI framework
ends up rendering them. Uses the same local-subprocess mock server as the
other protocol tests (``running_mcp_server``) -- no Docker, no browser.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mcp_toolcall_lab.reference_client import (
    OUTCOME_EMPTY,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    call_tool,
    discover_tools,
)
from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server


@pytest.mark.integration
async def test_discover_tools_matches_advertised_specs():
    with running_mcp_server() as server:
        tools = await discover_tools(server.url)

    specs = sorted(
        [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools],
        key=lambda spec: spec["name"],
    )
    assert specs == EXPECTED_TOOL_SPECS


@pytest.mark.integration
async def test_call_tool_success_is_classified_and_tagged():
    with running_mcp_server() as server:
        result = await call_tool(
            server.url,
            client="gradio",
            tool_name="find_municipalities",
            arguments={"query": "Yokohama"},
            prompt="Where is Yokohama?",
        )

    assert result.outcome == OUTCOME_SUCCESS
    assert result.error is None
    assert result.client == "gradio"
    assert result.prompt == "Where is Yokohama?"
    assert result.assistant_received_result is True
    assert result.payload == [{"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}]


@pytest.mark.integration
async def test_call_tool_empty_result_is_classified_separately_from_error():
    with running_mcp_server() as server:
        result = await call_tool(
            server.url,
            client="streamlit",
            tool_name="find_stations",
            arguments={"municipality_code": "00000"},
        )

    assert result.outcome == OUTCOME_EMPTY
    assert result.error is None
    assert result.payload == []


@pytest.mark.integration
async def test_call_tool_unknown_tool_is_an_error():
    with running_mcp_server() as server:
        result = await call_tool(
            server.url,
            client="gradio",
            tool_name="find_transaction_price",  # missing the trailing "s"
            arguments={"municipality_code": "14109", "year": 2025},
        )

    assert result.outcome == OUTCOME_ERROR
    assert result.error is not None
    assert result.assistant_received_result is False


@pytest.mark.integration
async def test_reference_calls_are_traceable_via_the_shared_log_with_client_and_case_id():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            result = await call_tool(
                server.url,
                client="gradio",
                tool_name="find_stations",
                arguments={"municipality_code": "14109"},
            )

        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    assert len(events) == 1
    event = events[0]
    assert event["meta"] == {"case_id": result.case_id, "client": "gradio", "source": "reference"}
