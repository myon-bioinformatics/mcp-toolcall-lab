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
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

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
        assert event["meta"] == {
            "call_id": trace.call_id,
            "chat_id": trace.chat_id,
            "source": "chat",
        }


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
        assert event["meta"] == {"source": "direct", "trace_id": "probe-1"}


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
async def test_every_logged_call_carries_a_duration() -> None:
    """duration_ms is wall-clock time for the whole call, not just the tool
    function -- see server.py's on_call_tool docstring for why it's measured
    around any MCP_TOOL_DELAY_SECONDS delay too, not only call_next()."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            await send_direct(server.url, tool_name="find_stations", arguments={"municipality_code": "14109"})

        event = _read_events(log_path)[0]
        assert isinstance(event["duration_ms"], (int, float))
        assert event["duration_ms"] >= 0


@pytest.mark.integration
async def test_a_real_chat_products_forwarded_chat_id_header_is_traceable() -> None:
    """A third delivery path, distinct from both of chat_sim.py's: a caller
    that sets no MCP `_meta` at all (unlike send_via_chat/send_direct, which
    both do by hand) but *does* send a known chat-correlation HTTP header --
    the shape a real chat product's own backend produces once it forwards
    its own conversation id (e.g. Open WebUI's ENABLE_FORWARD_USER_INFO_HEADERS
    + X-OpenWebUI-Chat-Id, confirmed against its own source). No test here
    drives an actual Open WebUI/LibreChat instance; it proves the header,
    once it arrives however it arrives, ends up in the same shared log."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            headers = {"X-OpenWebUI-Chat-Id": "chat_real_abc123", "X-OpenWebUI-Message-Id": "msg_xyz789"}
            async with streamablehttp_client(server.url, headers=headers) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool("find_municipalities", {"query": "Yokohama"})

        assert not result.isError
        event = _read_events(log_path)[0]
        assert event["meta"] == {
            "forwarded_headers": {"chat_id": "chat_real_abc123", "message_id": "msg_xyz789"}
        }


@pytest.mark.integration
async def test_a_plain_call_with_no_meta_and_no_forwarded_headers_logs_no_meta_key() -> None:
    """Neither hand-set _meta nor a forwarded header -- the common case for
    every non-chat protocol test in this repo -- should add a meta key at
    all, not an empty dict; test_chat_and_direct_paths_share_the_same_log_schema
    already relies on chat vs. direct calls having the *same* key set, and a
    call with genuinely nothing to report should look different from both."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            async with streamablehttp_client(server.url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await session.call_tool("find_stations", {"municipality_code": "14109"})

        event = _read_events(log_path)[0]
        assert "meta" not in event
