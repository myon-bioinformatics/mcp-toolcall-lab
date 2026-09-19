"""Trace a tool call from both delivery paths: chat-simulated and direct-to-MCP.

Every ``tools/call`` this mock server handles is logged to MCP_TOOLCALL_LOG
(when set) with whatever the caller put in the request's `_meta` field. That
means a "chat経由" call (through ``chat_sim.send_via_chat``, which builds the
same OpenAI-compatible tool_calls/tool-role messages Open WebUI and most
other chat UIs use) and a "MCPに直接" call (``chat_sim.send_direct``, or the
curl/SDK sessions in the other test modules) end up as directly comparable
JSONL rows — same schema, different `meta.source` — so a request's path from
chat message (or bare API call) through to the mock tool and back can be
reconstructed from one log file regardless of which door it came in.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mcp_toolcall_lab.chat_sim import send_direct, send_via_chat
from tests.test_streamable_http_protocol import running_mcp_server


def _read_events(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.integration
async def test_chat_path_is_traceable_via_the_shared_log():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            trace = await send_via_chat(
                server.url,
                user_text="Where is Yokohama?",
                tool_name="find_municipalities",
                arguments={"query": "Yokohama"},
            )

        # The OpenAI-compatible message shapes agree on the same call id.
        assert trace.assistant_tool_call_message["tool_calls"][0]["id"] == trace.call_id
        assert trace.tool_result_message["tool_call_id"] == trace.call_id
        assert "Yokohama" in trace.tool_result_message["content"]

        events = _read_events(log_path)
        assert len(events) == 1
        event = events[0]
        assert event["tool"] == "find_municipalities"
        assert event["outcome"] == "success"
        assert event["meta"]["call_id"] == trace.call_id
        assert event["meta"]["chat_id"] == trace.chat_id
        assert event["meta"]["source"] == "chat"
        assert event["meta"]["trace_id"] == trace.trace_id
        assert event["meta"]["request_id"] == trace.request_id


@pytest.mark.integration
async def test_direct_path_is_traceable_via_the_shared_log():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            result = await send_direct(
                server.url,
                tool_name="find_stations",
                arguments={"municipality_code": "14109"},
                trace_id="probe-1",
            )

        assert not result.isError
        events = _read_events(log_path)
        assert len(events) == 1
        event = events[0]
        assert event["tool"] == "find_stations"
        assert event["meta"]["source"] == "direct"
        assert event["meta"]["trace_id"] == "probe-1"
        assert event["meta"]["request_id"].startswith("req_")


@pytest.mark.integration
async def test_chat_and_direct_paths_share_the_same_log_schema():
    """Same row shape from either delivery path — only `meta` tells them apart."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            await send_via_chat(
                server.url,
                user_text="stations near Matsudo",
                tool_name="find_stations",
                arguments={"municipality_code": "12207"},
            )
            await send_direct(
                server.url,
                tool_name="find_stations",
                arguments={"municipality_code": "12207"},
            )

        chat_event, direct_event = _read_events(log_path)
        assert set(chat_event.keys()) == set(direct_event.keys())
        assert chat_event["meta"]["source"] == "chat"
        assert direct_event["meta"]["source"] == "direct"
        assert chat_event["result"] == direct_event["result"]


@pytest.mark.integration
async def test_chat_path_unknown_tool_is_traceable_as_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            trace = await send_via_chat(
                server.url,
                user_text="find the transaction price",
                tool_name="find_transaction_price",  # missing the trailing "s"
                arguments={"municipality_code": "14109", "year": 2025},
            )

        assert trace.mcp_result.isError
        events = _read_events(log_path)
        assert events[0]["outcome"] == "error"
        assert events[0]["meta"]["call_id"] == trace.call_id


@pytest.mark.integration
async def test_duration_ms_includes_the_artificial_tool_delay():
    """duration_ms is wall-clock time for the whole call as the caller
    experienced it. MCP_TOOL_DELAY_SECONDS exists specifically to simulate a
    slow call, so a duration that started its clock after that delay would
    under-report exactly the case the setting exists to create -- confirmed
    live before this fix: a 0.5s delay logged duration_ms=3.296, not ~500."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path), MCP_TOOL_DELAY_SECONDS="0.3") as server:
            await send_direct(server.url, tool_name="find_stations", arguments={"municipality_code": "14109"})

        event = _read_events(log_path)[0]
        assert event["duration_ms"] >= 300
