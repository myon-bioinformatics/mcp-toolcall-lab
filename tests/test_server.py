"""MCP server: session correlation, tracing, records, mocks, SDK boundaries, GitHub-ops tools and the Ironmate client."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import fastmcp.server.http as fastmcp_http
import httpx
import httpx2
import pytest
import uvicorn
from fastmcp import Client as FastMCPClient
from mcp import ClientSession, MCPError
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS, TOOL_DESCRIPTIONS
from mcp_toolcall_lab.chat_sim import new_call_id as sim_call_id, new_chat_id as sim_chat_id, send_direct, send_via_chat
from mcp_toolcall_lab.github_ops_server import create_mcp as create_mcp_github_ops_server, gh_ops
from mcp_toolcall_lab.ironmate_client import IronmateClient, normalize_catalog
from mcp_toolcall_lab.mcp_http import McpStdlibSession, extract_sse_data
from mcp_toolcall_lab.mock.common import (
    append_jsonl, chat_id_from_headers as chat_id_from_headers_mock_common, close_http11_sse, id_from_headers, new_call_id, new_chat_id,
    read_jsonl,
)
from mcp_toolcall_lab.prompt_experiment import (
    AUDIT_KEYS, DEFAULT_FIXTURE_DIR, MCP_INITIALIZE, MCP_INITIALIZED, MCP_TOOLS_CALL, MCP_TOOLS_LIST, VERDICT_FAIL, VERDICT_PASS, hop_method,
    hop_request, hop_response, hop_was_sent, iter_cases, iter_mcp_hops, load_case, main as main_prompt_experiment, mcp_call_hops, replay_case,
    replay_fixtures, request_header, response_header, tool_result_messages, write_audit,
)
from mcp_toolcall_lab.record import (
    chat_id_from_headers, mcp_tool_calls, new_call_id as record_call_id, new_chat_id as record_chat_id, read_jsonl as record_read_jsonl,
    record_call, record_protocol_event, resolve_correlation, usable_session_id,
)
from mcp_toolcall_lab.server import _session_id, create_mcp
from mcp_toolcall_lab.trace_probe import (
    FoundId, Hop, classify_value, cluster_hops, collect_hops, extract_ids_from_text, extract_ids_from_url, kinds_catalog, main, probe,
    resume_chat_path, snapshot_trace,
)
from tests.test_httpx_protocol import ACCEPT, McpHttpxSession
from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server


# ═══ · server ════════════════════════════════════════════════════════════════════════════════════════════
# ObservabilityMiddleware's session correlation must not fake a shared session.

def _context(session_id: object) -> SimpleNamespace:
    return SimpleNamespace(fastmcp_context=SimpleNamespace(session_id=session_id))


def test_session_id_is_none_when_fastmcp_context_missing() -> None:
    assert _session_id(SimpleNamespace(fastmcp_context=None)) is None


def test_session_id_is_none_when_underlying_session_id_is_none() -> None:
    assert _session_id(_context(None)) is None


def test_session_id_stringifies_a_real_session_id() -> None:
    assert _session_id(_context("sess-real")) == "sess-real"


def test_session_id_is_none_when_stringified_none_leaks_in() -> None:
    assert _session_id(_context("None")) is None
    assert _session_id(_context("")) is None


def test_two_no_session_calls_are_not_correlated() -> None:
    sessions: dict[str, str] = {}
    ctx = _context(None)

    first = resolve_correlation(
        meta={},
        headers={},
        session_id=_session_id(ctx),
        session_chats=sessions,
        mint=lambda: "chat_first",
    )
    second = resolve_correlation(
        meta={},
        headers={},
        session_id=_session_id(ctx),
        session_chats=sessions,
        mint=lambda: "chat_second",
    )

    assert first["chat_id"] == "chat_first"
    assert second["chat_id"] == "chat_second"
    assert first["chat_id_source"] == "minted"
    assert second["chat_id_source"] == "minted"
    assert "session_id" not in first
    assert "session_id" not in second
    assert sessions == {}
    assert "None" not in sessions

    leaked = resolve_correlation(
        meta={},
        headers={},
        session_id="None",
        session_chats=sessions,
        mint=lambda: "chat_leaked",
    )
    leaked_again = resolve_correlation(
        meta={},
        headers={},
        session_id="None",
        session_chats=sessions,
        mint=lambda: "chat_leaked_again",
    )
    assert leaked["chat_id"] == "chat_leaked"
    assert leaked_again["chat_id"] == "chat_leaked_again"
    assert leaked["chat_id_source"] == "minted"
    assert "None" not in sessions


# ═══ · trace ═════════════════════════════════════════════════════════════════════════════════════════════
# Trace a tool call from both delivery paths: chat-simulated and direct-to-MCP.
#
# Every ``tools/call`` this mock server handles is logged to MCP_TOOLCALL_LOG
# (when set) with whatever the caller put in the request's `_meta` field. That
# means a "chat経由" call (through ``chat_sim.send_via_chat``, which builds the
# same OpenAI-compatible tool_calls/tool-role messages Open WebUI and most
# other chat UIs use) and a "MCPに直接" call (``chat_sim.send_direct``, or the
# curl/SDK sessions in the other test modules) end up as directly comparable
# JSONL rows — same schema, different `meta.source` — so a request's path from
# chat message (or bare API call) through to the mock tool and back can be
# reconstructed from one log file regardless of which door it came in.

def _read_events(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.integration
async def test_chat_path_is_traceable_via_the_shared_log():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            trace = await send_via_chat(
                server.url,
                user_text="Where is Exampleville?",
                tool_name="find_municipalities",
                arguments={"query": "Exampleville"},
            )

        # The OpenAI-compatible message shapes agree on the same call id.
        assert trace.assistant_tool_call_message["tool_calls"][0]["id"] == trace.call_id
        assert trace.tool_result_message["tool_call_id"] == trace.call_id
        assert "Exampleville" in trace.tool_result_message["content"]

        events = mcp_tool_calls(_read_events(log_path))
        assert len(events) == 1
        event = events[0]
        assert event["tool"] == "find_municipalities"
        assert event["outcome"] == "success"
        assert event["meta"] == {
            "call_id": trace.call_id,
            "chat_id": trace.chat_id,
            "source": "chat",
        }
        assert event["chat_id"] == trace.chat_id
        assert event["debug"]["chat_id_source"] == "meta"
        assert event["event"] == "tools/call"
        assert "duration_ms" in event


@pytest.mark.integration
async def test_direct_path_is_traceable_via_the_shared_log():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            result = await send_direct(
                server.url,
                tool_name="find_stations",
                arguments={"municipality_code": "90001"},
                trace_id="probe-1",
            )

        assert not result.is_error
        events = mcp_tool_calls(_read_events(log_path))
        assert len(events) == 1
        event = events[0]
        assert event["tool"] == "find_stations"
        assert event["meta"] == {"source": "direct", "trace_id": "probe-1"}
        assert event["debug"]["chat_id_source"] in {"minted", "session", "header"}
        assert event["chat_id"]


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

        chat_event, direct_event = mcp_tool_calls(_read_events(log_path))
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
                arguments={"municipality_code": "90001", "year": 2025},
            )

        assert trace.mcp_result.is_error
        events = mcp_tool_calls(_read_events(log_path))
        assert events[0]["outcome"] == "error"
        assert events[0]["meta"]["call_id"] == trace.call_id


# ═══ · trace probe ═══════════════════════════════════════════════════════════════════════════════════════
# Stdlib tests for the ID trace probe — no MCP server, no browser.

def test_kinds_cover_reasoning_and_response_prefixes() -> None:
    kinds = {row["kind"] for row in kinds_catalog()}
    assert {"lab_chat_id", "call_id", "completion_id", "response_id", "reasoning_id"}.issubset(kinds)
    assert classify_value("chat_ab" + "c" * 20) == "lab_chat_id"
    assert classify_value("call_" + "a" * 24) == "call_id"
    assert classify_value("chatcmpl-abc123") == "completion_id"
    assert classify_value("resp_abc123") == "response_id"
    assert classify_value("rs_abc123") == "reasoning_id"
    assert classify_value("msg_abc123") == "responses_message_id"
    assert classify_value("fc_abc123") == "function_call_item_id"
    assert classify_value("66f012345678901234567890", key="chat_id") == "conversation_id"
    assert classify_value("chat_" + "d" * 24, key="chat_id") == "lab_chat_id"
    assert classify_value("sess-abcd-efgh", key="session_id") == "mcp_session_id"
    assert classify_value("req-initialize-1", key="request_id") == "mcp_request_id"


def test_resume_path_only_for_product_ids() -> None:
    assert resume_chat_path(None) is None
    assert resume_chat_path("new") is None
    assert resume_chat_path("chat_" + "a" * 24) is None
    assert resume_chat_path("chatcmpl-xyz") is None
    assert resume_chat_path("66f012345678901234567890") == "/c/66f012345678901234567890"


def test_url_harvests_librechat_and_owui_ids() -> None:
    libre = extract_ids_from_url(
        "http://127.0.0.1:3080/c/66f012345678901234567890/msgAAA?parentMessageId=parent1"
    )
    kinds = {item.kind: item.value for item in libre}
    assert kinds["conversation_id"] == "66f012345678901234567890"
    assert kinds["ui_message_id"] in {"msgAAA", "parent1"}
    assert any(item.kind == "parent_message_id" and item.value == "parent1" for item in libre)

    share = extract_ids_from_url("http://127.0.0.1:3000/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert share[0].kind == "share_id"
    assert extract_ids_from_url("http://127.0.0.1:3080/c/new") == []


def test_text_harvests_prefixed_ids() -> None:
    found = extract_ids_from_text(
        "reasoning rs_aaa then resp_bbb and leftover chatcmpl-ccc plus call_dddddddd"
    )
    by_kind = {item.kind: item.value for item in found}
    assert by_kind["reasoning_id"] == "rs_aaa"
    assert by_kind["response_id"] == "resp_bbb"
    assert by_kind["completion_id"] == "chatcmpl-ccc"
    assert by_kind["call_id"] == "call_dddddddd"


def test_shared_call_id_joins_mcp_and_openai_hops(tmp_path: Path) -> None:
    mcp = tmp_path / "mcp.jsonl"
    openai = tmp_path / "openai.jsonl"
    mcp.write_text(
        json.dumps(
            {
                "at": "2026-09-19T00:00:00+00:00",
                "tool": "find_municipalities",
                "outcome": "success",
                "meta": {
                    "chat_id": "chat_" + "a" * 24,
                    "call_id": "call_" + "b" * 24,
                    "source": "chat",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    openai.write_text(
        json.dumps(
            {
                "kind": "chat.completions",
                "completion_id": "chatcmpl-xyz",
                "call_ids": ["call_" + "b" * 24],
                "user": "Find municipalities named Exampleville",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    hops = collect_hops(mcp_log=mcp, openai_log=openai)
    report = probe(hops, needles={"chat_id": "chat_" + "a" * 24})
    assert report["matched"] is True
    assert report["match"] == "all"
    kinds = report["clusters"][0]["by_kind"]
    assert kinds["lab_chat_id"] == ["chat_" + "a" * 24]
    assert kinds["call_id"] == ["call_" + "b" * 24]
    assert kinds["completion_id"] == ["chatcmpl-xyz"]


def test_chat_id_plus_url_attaches_without_pretending_a_log_hit() -> None:
    hops = collect_hops(
        page_url="http://127.0.0.1:3080/c/66f012345678901234567890",
        labels={"lab_chat_id": "chat_" + "c" * 24},
    )
    report = probe(hops, needles={"chat_id": "chat_" + "c" * 24})
    assert report["matched"] is True
    kinds = report["clusters"][0]["by_kind"]
    assert kinds["conversation_id"] == ["66f012345678901234567890"]
    assert kinds["lab_chat_id"] == ["chat_" + "c" * 24]


def test_unknown_chat_id_does_not_match_empty_logs(tmp_path: Path) -> None:
    hops = collect_hops(mcp_log=tmp_path / "missing.jsonl")
    report = probe(hops, needles={"chat_id": "chat_" + "f" * 24})
    assert report["matched"] is False
    assert report["clusters"] == []


def test_snapshot_includes_labels_even_without_url() -> None:
    snap = snapshot_trace(labels={"lab_chat_id": "chat_" + "e" * 24, "trace_id": "probe-9"})
    assert snap["by_kind"]["lab_chat_id"] == ["chat_" + "e" * 24]
    assert snap["by_kind"]["trace_id"] == ["probe-9"]


def test_orphan_hops_stay_separate() -> None:
    hops = [
        Hop(source="mcp_log", ids=[FoundId("lab_chat_id", "chat_" + "1" * 24, "mcp_log")]),
        Hop(source="openai_log", ids=[FoundId("completion_id", "chatcmpl-other", "openai_log")]),
    ]
    clusters = cluster_hops(hops)
    assert len(clusters) == 2


def test_cli_kinds_and_probe(tmp_path: Path) -> None:
    mcp = tmp_path / "mcp.jsonl"
    mcp.write_text(
        json.dumps({"meta": {"chat_id": "chat_" + "a" * 24, "call_id": "call_" + "b" * 24}}) + "\n",
        encoding="utf-8",
    )
    kinds_proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.trace_probe", "kinds"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(kinds_proc.stdout)
    assert any(row["kind"] == "reasoning_id" for row in payload["kinds"])

    probe_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_toolcall_lab.trace_probe",
            "--chat-id",
            "chat_" + "a" * 24,
            "--mcp-log",
            str(mcp),
            "--openai-log",
            str(tmp_path / "missing-openai.jsonl"),
            "--observe",
            str(tmp_path / "missing-obs.jsonl"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(probe_proc.stdout)
    assert probe_proc.returncode == 0
    assert report["matched"] is True
    assert report["clusters"][0]["by_kind"]["call_id"] == ["call_" + "b" * 24]


def test_main_exit_code_on_miss(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--chat-id",
            "chat_" + "z" * 24,
            "--mcp-log",
            str(tmp_path / "nope.jsonl"),
            "--openai-log",
            str(tmp_path / "nope-o.jsonl"),
            "--observe",
            str(tmp_path / "nope-a.jsonl"),
        ]
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["matched"] is False


# ═══ · record ════════════════════════════════════════════════════════════════════════════════════════════
# MCP log correlation: chat_id from meta, headers, session, or mint.

def test_header_keys_are_case_insensitive() -> None:
    assert chat_id_from_headers({"X-Chat-Id": "chat_from_header"}) == "chat_from_header"
    assert chat_id_from_headers({"X-OpenWebUI-Chat-Id": "owui-real-chat"}) == "owui-real-chat"
    assert chat_id_from_headers({"x-conversation-id": "66f012345678901234567890"}) == "66f012345678901234567890"
    assert chat_id_from_headers({"accept": "application/json"}) is None


def test_resolve_prefers_meta_then_header_then_session_then_mint() -> None:
    sessions: dict[str, str] = {}
    meta_hit = resolve_correlation(
        meta={"chat_id": "chat_meta", "call_id": "call_1", "source": "chat"},
        headers={"x-chat-id": "chat_header"},
        session_id="sess-1",
        session_chats=sessions,
        mint=lambda: "chat_minted",
    )
    assert meta_hit["chat_id"] == "chat_meta"
    assert meta_hit["chat_id_source"] == "meta"
    assert meta_hit["call_id"] == "call_1"
    assert sessions["sess-1"] == "chat_meta"

    header_hit = resolve_correlation(
        meta={},
        headers={"X-Lab-Chat-Id": "chat_header"},
        session_id="sess-2",
        session_chats=sessions,
        mint=lambda: "chat_minted",
    )
    assert header_hit["chat_id_source"] == "header"
    assert header_hit["chat_id"] == "chat_header"

    owui = resolve_correlation(
        meta={},
        headers={
            "X-OpenWebUI-Chat-Id": "owui-chat-row",
            "X-OpenWebUI-Message-Id": "owui-msg-row",
        },
        mint=lambda: "chat_minted",
    )
    assert owui["chat_id"] == "owui-chat-row"
    assert owui["chat_id_source"] == "header"
    assert owui["message_id"] == "owui-msg-row"

    session_hit = resolve_correlation(
        meta={},
        headers={},
        session_id="sess-2",
        session_chats=sessions,
        mint=lambda: "chat_should_not_run",
    )
    assert session_hit["chat_id_source"] == "session"
    assert session_hit["chat_id"] == "chat_header"

    minted = resolve_correlation(
        meta={},
        headers=None,
        session_id="sess-3",
        session_chats=sessions,
        mint=lambda: "chat_minted",
    )
    assert minted["chat_id_source"] == "minted"
    assert minted["chat_id"] == "chat_minted"
    assert minted["call_id_source"] == "minted"
    assert str(minted["call_id"]).startswith("call_")
    assert sessions["sess-3"] == "chat_minted"


def test_usable_session_id_keeps_missingness() -> None:
    assert usable_session_id(None) is None
    assert usable_session_id("") is None
    assert usable_session_id("   ") is None
    assert usable_session_id("None") is None
    assert usable_session_id("sess-real") == "sess-real"


def test_record_call_keeps_meta_and_adds_debug(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    record_call(
        tool="find_municipalities",
        arguments={"query": "Exampleville"},
        outcome="success",
        result=[{"name": "Exampleville"}],
        meta={"chat_id": "chat_wire", "source": "stub-front"},
        debug={"chat_id": "chat_wire", "chat_id_source": "meta", "message_id": "owui-msg-row"},
        duration_ms=1.5,
    )
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["event"] == "tools/call"
    assert row["chat_id"] == "chat_wire"
    assert row["meta"] == {"chat_id": "chat_wire", "source": "stub-front"}
    assert row["debug"]["chat_id_source"] == "meta"
    assert row["debug"]["message_id"] == "owui-msg-row"
    assert row["message_id"] == "owui-msg-row"
    assert row["debug"]["result_n"] == 1
    assert row["duration_ms"] == 1.5
    monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)
    assert os.environ.get("MCP_TOOLCALL_LOG") is None


def test_protocol_events_are_not_tool_calls(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    record_protocol_event(
        event="initialize",
        debug={"session_id": "sess-1", "request_id": "req-1"},
    )
    record_call(
        tool="find_municipalities",
        arguments={"query": "Exampleville"},
        outcome="success",
        result=[{"name": "Exampleville"}],
        debug={"session_id": "sess-1", "request_id": "req-2"},
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "initialize"
    assert "tool" not in events[0]
    assert mcp_tool_calls(events) == [events[1]]


@pytest.mark.integration
def test_live_server_takes_x_chat_id_then_reuses_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "toolcalls.jsonl"
        with running_mcp_server(MCP_TOOLCALL_LOG=str(log_path)) as server:
            session = McpHttpxSession(server.url)
            session.initialize()
            headers = {
                "Content-Type": "application/json",
                "Accept": ACCEPT,
                "Mcp-Session-Id": session.session_id or "",
                "X-Chat-Id": "chat_from_ui",
                "X-OpenWebUI-Message-Id": "owui-msg-live",
            }
            session.client.post(
                session.url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "find_municipalities",
                        "arguments": {"query": "Exampleville"},
                    },
                },
            )
            session.client.post(
                session.url,
                headers={
                    k: v
                    for k, v in headers.items()
                    if k.lower() not in {"x-chat-id", "x-openwebui-message-id"}
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "find_stations",
                        "arguments": {"municipality_code": "90001"},
                    },
                },
            )
            session.close()
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert "initialize" in [event.get("event") for event in events]
        calls = mcp_tool_calls(events)
        assert calls[0]["debug"]["chat_id_source"] == "header"
        assert calls[0]["chat_id"] == "chat_from_ui"
        assert calls[0]["debug"]["message_id"] == "owui-msg-live"
        assert calls[0]["message_id"] == "owui-msg-live"
        assert calls[1]["chat_id"] == "chat_from_ui"
        assert calls[1]["debug"]["chat_id_source"] == "session"
        assert "message_id" not in calls[1]["debug"]
        assert "message_id" not in calls[1]
        assert calls[0]["debug"].get("request_id")
        assert calls[0]["debug"].get("session_id")


# ═══ · mock common ═══════════════════════════════════════════════════════════════════════════════════════
# Shared mock helpers — stdlib only, no FastMCP, no OpenAI decision logic.

def test_mints_and_jsonl_reader_live_in_mock_common() -> None:
    assert record_chat_id is new_chat_id
    assert record_call_id is new_call_id
    assert sim_chat_id is new_chat_id
    assert sim_call_id is new_call_id
    assert record_read_jsonl is read_jsonl
    assert new_chat_id().startswith("chat_")
    assert new_call_id().startswith("call_")


def test_header_lookup_is_case_insensitive() -> None:
    assert chat_id_from_headers_mock_common({"X-Chat-Id": "chat_from_header"}) == "chat_from_header"
    assert chat_id_from_headers_mock_common({"X-OpenWebUI-Chat-Id": "owui-real-chat"}) == "owui-real-chat"
    assert id_from_headers({"X-Message-Id": "m1"}, ("x-message-id",)) == "m1"
    assert chat_id_from_headers_mock_common({"accept": "application/json"}) is None


def test_append_jsonl_creates_parent_and_read_skips_junk(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    append_jsonl(path, {"n": 1})
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n\n", encoding="utf-8")
    append_jsonl(path, {"n": 2})
    events = read_jsonl(path)
    assert [row["n"] for row in events] == [1, 2]


def test_close_http11_sse_sets_connection_close() -> None:
    class _Handler:
        def __init__(self) -> None:
            self.headers: list[tuple[str, str]] = []
            self.close_connection = False

        def send_header(self, key: str, value: str) -> None:
            self.headers.append((key, value))

    handler = _Handler()
    close_http11_sse(handler)
    assert ("Connection", "close") in handler.headers
    assert handler.close_connection is True


# ═══ · openai toolcall mock ══════════════════════════════════════════════════════════════════════════════

DEMOS = ROOT / "demos"
if str(DEMOS) not in sys.path:
    sys.path.insert(0, str(DEMOS))

import openai_toolcall_mock as mock  # noqa: E402


def test_no_tools_is_plain_text_for_picker_off() -> None:
    message = mock.decide_assistant_message(
        {"messages": [{"role": "user", "content": "Find municipalities named Exampleville"}]}
    )
    assert message.get("tool_calls") is None
    assert "No MCP tools were attached" in message["content"]


def test_librechat_prefixed_tool_name_emits_tool_call() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Exampleville"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "find_municipalities_mcp_lab",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    message = mock.decide_assistant_message(body)
    assert message["tool_calls"][0]["function"]["name"] == "find_municipalities_mcp_lab"
    assert "Exampleville" in message["tool_calls"][0]["function"]["arguments"]


def test_tool_result_turn_returns_exampleville() -> None:
    message = mock.decide_assistant_message(
        {
            "messages": [
                {"role": "user", "content": "Find municipalities named Exampleville"},
                {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
                {"role": "tool", "tool_call_id": "call_1", "content": '[{"name":"Exampleville"}]'},
            ]
        }
    )
    assert "Exampleville" in message["content"]
    assert message.get("tool_calls") is None


def test_completion_and_call_ids_are_prefixed() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Exampleville"}],
        "tools": [{"type": "function", "function": {"name": "find_municipalities_mcp_lab"}}],
    }
    message = mock.decide_assistant_message(body)
    completion = mock._completion(message, completion_id="chatcmpl-fixed")
    assert completion["id"] == "chatcmpl-fixed"
    call_ids = mock.tool_call_ids_from_message(message)
    assert len(call_ids) == 1
    assert call_ids[0].startswith("call_")
    inbound = mock.inbound_call_ids_from_messages(
        [
            {"role": "assistant", "tool_calls": [{"id": "call_inbound"}]},
            {"role": "tool", "tool_call_id": "call_inbound"},
        ]
    )
    assert inbound == ["call_inbound"]


def test_openwebui_prefixed_tool_name_emits_tool_call() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Exampleville"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lab_find_municipalities",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    message = mock.decide_assistant_message(body)
    assert message["tool_calls"][0]["function"]["name"] == "lab_find_municipalities"
    projected = mock.wire_messages_projection(
        [
            {"role": "user", "content": "Find municipalities named Exampleville"},
            message,
            {"role": "tool", "tool_call_id": message["tool_calls"][0]["id"], "content": "[]"},
        ]
    )
    assert projected[0]["role"] == "user"
    assert projected[1]["tool_calls"][0]["id"].startswith("call_")
    assert projected[2]["tool_call_id"] == message["tool_calls"][0]["id"]


def test_stream_branch_closes_http11_connection() -> None:
    """Keep-alive SSE never ends; LibreChat would hang on the next reuse."""
    source = Path(mock.__file__).read_text(encoding="utf-8")
    assert "close_http11_sse" in source
    assert "MESSAGE_ID_HEADER_KEYS" in source
    common = (ROOT / "src" / "mcp_toolcall_lab" / "mock" / "common.py").read_text(
        encoding="utf-8"
    )
    assert 'send_header("Connection", "close")' in common
    assert "close_connection = True" in common
    assert mock.close_http11_sse is close_http11_sse


def test_openai_mock_uses_shared_call_id_mint() -> None:
    assert mock.new_call_id is new_call_id


def test_openai_log_copies_chat_id_header(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "nested" / "openai.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    mock._log({"kind": "chat.completions"}, headers={"X-Chat-Id": "chat_from_ui"})
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["kind"] == "chat.completions"
    assert row["chat_id"] == "chat_from_ui"


def test_openai_log_copies_message_id_header(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "openai.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    mock._log(
        {"kind": "chat.completions"},
        headers={"X-OpenWebUI-Message-Id": "owui-msg-row"},
    )
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["message_id"] == "owui-msg-row"


def test_content_metrics_strings_parts_empty_and_invalid():
    result = mock.message_content_metrics([
        {"role": "user", "content": "秘密abc"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"},
                                         {"type": "input_text", "text": "日本"}]},
        {"role": "tool", "content": "TOOL_SECRET"},
        {"role": "system", "content": None},
    ])
    assert result["content_chars"] == 5 + 4 + 11
    assert result["role_content_chars"]["assistant"] == 4
    assert result["role_content_chars"]["system"] == 0
    assert result["tool_content_chars"] == 11
    assert "秘密" not in json.dumps(result, ensure_ascii=False)
    assert "TOOL_SECRET" not in json.dumps(result)
    assert mock.message_content_metrics([{"role": "user", "content": []}])["content_chars"] == 0
    for bad in ([None], [{"role": "user", "content": {"text": "private"}}],
                [{"role": "unknown", "content": "secret"}],
                [{"role": "tool", "content": [{"type": "image_url", "image_url": "secret"}]}]):
        assert mock.message_content_metrics(bad) == {"content_measurement": "invalid_messages"}


def test_logged_measurement_never_includes_message_bodies(tmp_path, monkeypatch):
    log = tmp_path / "metrics.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    secret = "UNIQUE_PRIVATE_TOOL_RESULT"
    messages = [{"role": "user", "content": "PRIVATE_PROMPT"},
                {"role": "tool", "content": secret}]
    mock._log({"kind": "chat.completions", "completion_id": "chatcmpl-test",
               "wire_messages": mock.wire_messages_projection(messages),
               **mock.message_content_metrics(messages)})
    raw = log.read_text(encoding="utf-8")
    assert "PRIVATE_PROMPT" not in raw and secret not in raw
    record = json.loads(raw)
    assert "user" not in record
    assert record["content_chars"] == len("PRIVATE_PROMPT") + len(secret)
    assert "chat_id" not in record


def _history_with_arguments(arguments, content=None):
    return [{"role": "assistant", "content": content, "tool_calls": [
        {"id": "call_one", "type": "function", "function": {
            "name": "lookup", "arguments": arguments}},
        {"id": "call_two", "type": "function", "function": {
            "name": "lookup", "arguments": "日本🙂"}},
    ]}, {"role": "tool", "tool_call_id": "call_one", "content": "SECRET_RESULT"}]


def test_full_message_metrics_count_arguments_unicode_and_extra_fields():
    for content in (None, ""):
        messages = _history_with_arguments("a" * 10, content)
        messages[0]["extra"] = "秘密🙂"
        before = json.dumps(messages, ensure_ascii=False)
        small = mock.message_content_metrics(messages)
        messages[0]["tool_calls"][0]["function"]["arguments"] = "a" * 1000
        large = mock.message_content_metrics(messages)
        assert large["message_json_chars"] - small["message_json_chars"] == 990
        assert large["tool_call_argument_chars"] - small["tool_call_argument_chars"] == 990
        assert small["tool_call_argument_chars"] == 13
        assert small["content_chars"] == large["content_chars"] == len("SECRET_RESULT")
        assert small["role_content_chars"]["assistant"] == 0
        assert small["message_json_format"] == "unicode_sorted_compact_v1"
        restored = json.loads(before)
        assert small["message_json_chars"] == len(json.dumps(
            restored, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
        reordered = [dict(reversed(list(m.items()))) for m in restored]
        assert mock.message_content_metrics(reordered) == small
        assert "秘密" not in json.dumps(small, ensure_ascii=False)
    assert mock.message_content_metrics([])["message_json_chars"] == 2
    # Even non-JSON arguments remain opaque strings and are measured, not executed.
    assert mock.message_content_metrics(_history_with_arguments("{broken"))["tool_call_argument_chars"] == 10


def test_invalid_tool_call_structures_fail_closed_without_payload():
    for bad_calls in (None, {}, "SECRET", [None], [{"type": "function", "id": "c"}],
                      [{"type": "function", "id": "c", "function": "SECRET"}],
                      [{"type": "custom", "id": "c", "function": {}}]):
        assert mock.message_content_metrics([{"role": "assistant", "tool_calls": bad_calls}]) == {
            "content_measurement": "invalid_messages"}
    for arguments in (None, {}, [], 17, True):
        assert mock.message_content_metrics(_history_with_arguments(arguments)) == {
            "content_measurement": "invalid_messages"}
    messages = _history_with_arguments("SECRET")
    messages[0]["role"] = "user"
    assert mock.message_content_metrics(messages) == {"content_measurement": "invalid_messages"}
    messages = _history_with_arguments("SECRET")
    messages[0]["extra"] = float("nan")
    assert mock.message_content_metrics(messages) == {"content_measurement": "invalid_messages"}


def test_http_argument_only_growth_is_logged_without_secrets(tmp_path, monkeypatch):
    import threading
    import urllib.request
    import urllib.error
    from http.server import ThreadingHTTPServer
    from mcp_toolcall_lab.history_growth import measure

    log = tmp_path / "ingress.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    server = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    completions = []
    try:
        for size in (10, 1000):
            messages = _history_with_arguments("SECRET_ARGUMENT" + "a" * size)
            messages.insert(0, {"role": "user", "content": "SECRET_PROMPT"})
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                data=json.dumps({"messages": messages}).encode(),
                headers={"Content-Type": "application/json", "X-Chat-Id": "chat-A",
                         "Authorization": "Bearer SECRET_TOKEN"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                completions.append(json.load(response)["id"])
        request.data = json.dumps({"messages": _history_with_arguments({"secret": "SECRET_BAD"})}).encode()
        try:
            urllib.request.urlopen(request, timeout=5)
            assert False, "malformed arguments accepted"
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert "SECRET_BAD" not in error.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    raw = log.read_text(encoding="utf-8")
    assert all(secret not in raw for secret in (
        "SECRET_ARGUMENT", "SECRET_RESULT", "SECRET_PROMPT", "SECRET_TOKEN", "SECRET_BAD"))
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 2
    small, large = measure(rows)
    assert [r["completion_id"] for r in rows] == completions
    assert [(r["chat_id"], r["turn_index"]) for r in (small, large)] == [("chat-A", 1), ("chat-A", 2)]
    assert large["message_json_chars"] - small["message_json_chars"] == 990
    assert large["tool_call_argument_chars"] - small["tool_call_argument_chars"] == 990
    assert large["projection_json_chars"] == small["projection_json_chars"]
    assert large["content_chars"] == small["content_chars"]


# ═══ · mcp http ══════════════════════════════════════════════════════════════════════════════════════════
# Stdlib MCP session follows the product handshake: initialize → tools/list → tools/call.

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


# ═══ · sdk transport boundaries ══════════════════════════════════════════════════════════════════════════
# Exercise SDK upgrade boundaries through the lab's real FastMCP HTTP app.
#
# The independent ``fastmcp`` package still inherits the official SDK's HTTP
# session manager. Short idle limits below are test-only; production defaults
# and the chat/direct callers are left untouched.

SDK_TRANSPORT_BOUNDARIES_MARK = pytest.mark.integration
IDLE_TIMEOUT = 0.2


@asynccontextmanager
async def serve(app):
    """Bind a real loopback socket so open GET streams are not ASGI-buffered."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


def _recording_client() -> tuple[httpx2.AsyncClient, list[str]]:
    """Capture MCP session ids. The SDK client no longer yields a session getter."""
    seen: list[str] = []

    async def remember(response: httpx2.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            seen.append(session_id)

    return httpx2.AsyncClient(event_hooks={"response": [remember]}), seen


async def call(url, path):
    kwargs = {"tool_name": "find_municipalities", "arguments": {"query": "Exampleville"}}
    if path == "chat":
        trace = await send_via_chat(url, user_text="Where is Exampleville?", **kwargs)
        assert trace.tool_result_message["role"] == "tool"
        assert trace.tool_result_message["tool_call_id"] == trace.call_id
        return trace.mcp_result
    return await send_direct(url, **kwargs)


def leaves(exc):
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for child in exc.exceptions for leaf in leaves(child)]
    return [exc]


def redirect_app(location):
    async def redirect(request):
        return RedirectResponse(app.state.location, status_code=307)

    app = Starlette(routes=[Route("/redirect", redirect, methods=["POST", "GET", "DELETE"])])
    app.state.location = location
    return app


@SDK_TRANSPORT_BOUNDARIES_MARK
@pytest.mark.parametrize("path", ["chat", "direct"])
async def test_same_origin_redirect_preserves_tool_call(path):
    app = create_mcp().http_app(path="/mcp")
    app.router.routes.extend(redirect_app("/mcp").routes)
    async with serve(app) as origin:
        async with asyncio.timeout(5):
            result = await call(origin + "/redirect", path)
        assert not result.is_error
        assert "Exampleville" in result.content[0].text


@SDK_TRANSPORT_BOUNDARIES_MARK
@pytest.mark.parametrize("path", ["chat", "direct"])
@pytest.mark.parametrize("changed", ["host", "port"])
async def test_cross_origin_redirect_fails_without_contacting_target(path, changed):
    hits = []

    async def sink(request):
        hits.append(request.method)
        return Response(status_code=500)

    sink_app = Starlette(routes=[Route("/mcp", sink, methods=["POST", "GET", "DELETE"])])
    async with serve(sink_app) as target:
        app = redirect_app(target + "/mcp")
        async with serve(app) as origin:
            # For the host case keep the source port: only the hostname differs.
            if changed == "host":
                app.state.location = origin.replace("127.0.0.1", "localhost") + "/mcp"
                app.router.routes.append(Route("/mcp", sink, methods=["POST", "GET", "DELETE"]))
            async with asyncio.timeout(5):
                with pytest.raises(ExceptionGroup) as caught:
                    await call(origin + "/redirect", path)
            errors = leaves(caught.value)
            assert errors and all(isinstance(error, MCPError) for error in errors)
            assert all("not followed" in str(error) for error in errors)
        assert hits == []


@pytest.fixture
def short_idle_sessions(monkeypatch):
    """Use FastMCP's real inherited manager, accelerating only its idle clock."""
    manager_type = fastmcp_http.FastMCPStreamableHTTPSessionManager
    managers = []

    class ShortIdleManager(manager_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Verify this applies to our independent FastMCP server too.
            assert self.session_idle_timeout == 1800
            assert self.max_sessions == 10_000
            self.session_idle_timeout = IDLE_TIMEOUT
            managers.append(self)

    monkeypatch.setattr(fastmcp_http, "FastMCPStreamableHTTPSessionManager", ShortIdleManager)
    return managers


@SDK_TRANSPORT_BOUNDARIES_MARK
async def test_sdk_get_stream_keeps_session_alive(short_idle_sessions):
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        client, seen = _recording_client()
        async with client:
            async with streamable_http_client(origin + "/mcp", http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert seen
                    original = seen[-1]
                    await session.list_tools()
                    await asyncio.sleep(IDLE_TIMEOUT * 3)
                    result = await session.call_tool("find_municipalities", {"query": "Exampleville"})
                    assert not result.is_error
                    assert seen[-1] == original


@SDK_TRANSPORT_BOUNDARIES_MARK
async def test_idle_http_session_returns_404_then_fresh_chat_succeeds(short_idle_sessions):
    """POST-only clients can expire; a new chat turn initializes a fresh session."""
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/json, text/event-stream"}
            opened = await client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "idle-boundary", "version": "1"}},
            })
            opened.raise_for_status()
            old_id = opened.headers["mcp-session-id"]
            headers["Mcp-Session-Id"] = old_id
            initialized = await client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            assert initialized.status_code == 202
            await asyncio.sleep(IDLE_TIMEOUT * 3)
            expired = await client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            })
            assert expired.status_code == 404
        result = await call(url, "chat")
        assert not result.is_error
        assert "Exampleville" in result.content[0].text


@SDK_TRANSPORT_BOUNDARIES_MARK
async def test_deleted_sdk_session_surfaces_error_then_new_connection_succeeds():
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        http, seen = _recording_client()
        async with http:
            async with streamable_http_client(url, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert seen
                    old_id = seen[-1]
                    async with httpx.AsyncClient() as probe:
                        deleted = await probe.delete(url, headers={"Mcp-Session-Id": old_id})
                        assert deleted.status_code == 200
                    async with asyncio.timeout(5):
                        with pytest.raises(MCPError, match="Session not found"):
                            await session.call_tool("find_municipalities", {"query": "Exampleville"})
        result = await call(url, "direct")
        assert not result.is_error
        assert "Exampleville" in result.content[0].text


@SDK_TRANSPORT_BOUNDARIES_MARK
async def test_session_capacity_error_preserves_existing_connection(short_idle_sessions):
    async with serve(create_mcp().http_app(path="/mcp")) as origin:
        url = origin + "/mcp"
        short_idle_sessions[0].max_sessions = 1
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async with asyncio.timeout(5):
                    with pytest.raises(ExceptionGroup) as caught:
                        await call(url, "chat")
                errors = leaves(caught.value)
                assert errors and all(isinstance(error, MCPError) for error in errors)
                assert all("Too many open sessions" in str(error) for error in errors)
                assert (await session.list_tools()).tools
        # Closing the existing SDK transport sends DELETE and frees capacity.
        assert not (await call(url, "direct")).is_error


# ═══ · github ops server ═════════════════════════════════════════════════════════════════════════════════
# GitHub-ops FastMCP tools, called through FastMCP's in-memory Client (no network).
#
# Mirrors browser-test-kit's tests/python/test_gh_ops.py stub shape (Stub /
# client_for / reply) so the transport-stubbing style stays recognizable across
# both repos, but drives calls through ``fastmcp.Client(transport=mcp)`` --
# the same in-memory pattern ``tests/test_schema.py`` uses via
# ``mcp.list_tools()`` -- instead of importing gh_ops functions directly.

REPO = "octo/demo"
HEAD = "ecfd0ba1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7"


def reply(data=None, status=200, link=None):
    return gh_ops.Response(status, data, {"link": link} if link else {})


class Stub:
    """Transport stub: routes (METHOD, path) to a reply, a reply list, or a callable."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        self.calls.append(
            {
                "method": method,
                "path": parts.path,
                "query": urllib.parse.parse_qs(parts.query),
                "body": body,
                "headers": headers,
            }
        )
        route = self.routes[(method, parts.path)]
        if callable(route):
            return route(parts, body)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route

    @property
    def methods(self):
        return [call["method"] for call in self.calls]


def client_for(routes, token="t0ken"):
    stub = Stub(routes)
    return gh_ops.Client(token=token, transport=stub), stub


def pr_payload(**overrides):
    data = {
        "state": "open",
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "draft": False,
        "head": {"sha": HEAD, "ref": "feature"},
        "base": {"ref": "main"},
        "commits": 2,
        "changed_files": 3,
        "additions": 10,
        "deletions": 2,
        "html_url": "https://github.com/octo/demo/pull/11",
        "body": "",
    }
    data.update(overrides)
    return data


def comment(index, author="alice", body=None):
    return {
        "id": 100 + index,
        "created_at": f"2026-09-2{index % 10}T00:00:00Z",
        "user": {"login": author},
        "body": body if body is not None else f"comment {index}",
        "html_url": f"https://x/{index}",
    }


@pytest.fixture
def wired(monkeypatch):
    """Build a fresh server; return (mcp, patch_client) where patch_client(routes) makes
    every tool call in this test use a stubbed gh_ops.Client with the given routes."""
    import mcp_toolcall_lab.github_ops_server as gos

    mcp = create_mcp_github_ops_server()

    def patch_client(routes, token="t0ken"):
        client, stub = client_for(routes, token=token)
        monkeypatch.setattr(gos, "_client", lambda: client)
        return client, stub

    return mcp, patch_client


async def _call(mcp, name, arguments):
    async with FastMCPClient(transport=mcp) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)
    return result


def _data(result):
    return result.structured_content["result"] if "result" in (result.structured_content or {}) else result.structured_content


@pytest.mark.parametrize("failure_stage", ["import", "register"])
@pytest.mark.parametrize("trace_path", [None, "", "/private/trace.jsonl"])
async def test_observability_failure_is_nonfatal_and_warns_only_when_enabled(
    monkeypatch, capsys, failure_stage, trace_path
):
    import mcp_toolcall_lab.github_ops_server as gos

    if trace_path is None:
        monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)
    else:
        monkeypatch.setenv("MCP_TOOLCALL_LOG", trace_path)
    if failure_stage == "import":
        monkeypatch.setitem(
            sys.modules, "mcp_toolcall_lab.server", ModuleType("unavailable_server")
        )
    else:
        def fail_registration(self, middleware):
            raise RuntimeError("secret-token /private/trace.jsonl\nforged log row")

        monkeypatch.setattr(gos.FastMCP, "add_middleware", fail_registration)

    mcp = gos.create_mcp()
    captured = capsys.readouterr()
    assert captured.out == ""
    if trace_path:
        lines = captured.err.splitlines()
        assert len(lines) == 1
        stamp, message = lines[0].split(" ", 1)
        assert datetime.fromisoformat(stamp.replace("Z", "+00:00")).utcoffset().total_seconds() == 0
        assert stamp.endswith("Z")
        assert message.startswith("WARNING github_ops observability middleware unavailable")
        assert "ImportError" in message if failure_stage == "import" else "RuntimeError" in message
        assert "secret-token" not in captured.err
        assert "/private/" not in captured.err
        assert "forged log row" not in captured.err
    else:
        assert captured.err == ""

    # The warning must not replace tool registration or break the MCP transport.
    async with FastMCPClient(transport=mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert {"pr_status", "resolve_public", "url_compare"} <= names
        result = await client.call_tool(
            "url_compare", {"repo": REPO, "base": "main", "head": "feature"}
        )
        assert result.is_error is False
        assert _data(result)["web"] == "https://github.com/octo/demo/compare/main...feature"


async def test_observability_success_does_not_warn(monkeypatch, capsys):
    import mcp_toolcall_lab.github_ops_server as gos

    monkeypatch.setenv("MCP_TOOLCALL_LOG", "/private/trace.jsonl")
    registered = []
    original = gos.FastMCP.add_middleware

    def record_registration(self, middleware):
        registered.append(middleware)
        return original(self, middleware)

    monkeypatch.setattr(gos.FastMCP, "add_middleware", record_registration)
    gos.create_mcp()
    assert len(registered) == 1
    assert registered[0].__class__.__name__ == "ObservabilityMiddleware"
    assert capsys.readouterr().err == ""


# --- pr_status --------------------------------------------------------------------

async def test_pr_status_tool(wired):
    mcp, patch_client = wired
    patch_client({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload())})
    result = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    data = _data(result)
    assert data["ok"] is True
    assert data["state"] == "open" and data["mergeable_state"] == "clean"
    assert data["head_sha"] == HEAD
    assert data["url"] == "https://github.com/octo/demo/pull/11"
    assert result.is_error is False


# --- pr_for_branch ------------------------------------------------------------------

async def test_pr_for_branch_tool_found(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls"): reply(
                [
                    {
                        "number": 11,
                        "state": "open",
                        "merged_at": None,
                        "draft": False,
                        "head": {"sha": HEAD},
                        "base": {"ref": "main"},
                        "title": "feature",
                        "html_url": "https://github.com/octo/demo/pull/11",
                    }
                ]
            )
        }
    )
    result = await _call(mcp, "pr_for_branch", {"repo": REPO, "branch": "feature"})
    data = _data(result)
    assert data["ok"] is True
    assert data["pulls"][0]["number"] == 11
    assert data["head"] == "octo:feature"


async def test_pr_for_branch_tool_none_found(wired):
    mcp, patch_client = wired
    patch_client({("GET", "/repos/octo/demo/pulls"): reply([])})
    result = await _call(mcp, "pr_for_branch", {"repo": REPO, "branch": "gone"})
    data = _data(result)
    assert data["ok"] is False
    assert data["pulls"] == []


# --- open_prs -------------------------------------------------------------------------

async def test_open_prs_tool_via_search(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", "/search/issues"): reply(
                {
                    "total_count": 1,
                    "items": [
                        {
                            "repository_url": "https://api.github.com/repos/octo/demo",
                            "number": 5,
                            "draft": False,
                            "updated_at": "2026-09-20T00:00:00Z",
                            "user": {"login": "alice"},
                            "title": "fix thing",
                            "html_url": "https://github.com/octo/demo/pull/5",
                        }
                    ],
                }
            )
        }
    )
    result = await _call(mcp, "open_prs", {"owner": "octo"})
    data = _data(result)
    assert data["ok"] is True
    assert data["pulls"][0]["repo"] == "octo/demo"


async def test_open_prs_tool_repo_scoped(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls"): reply(
                [
                    {
                        "number": 7,
                        "draft": True,
                        "updated_at": "2026-09-21T00:00:00Z",
                        "user": {"login": "bob"},
                        "title": "wip",
                        "html_url": "https://github.com/octo/demo/pull/7",
                    }
                ]
            )
        }
    )
    result = await _call(mcp, "open_prs", {"owner": "octo", "repos": ["demo"]})
    data = _data(result)
    assert data["ok"] is True
    assert data["pulls"] == [
        {
            "repo": "octo/demo",
            "number": 7,
            "draft": True,
            "updated_at": "2026-09-21T00:00:00Z",
            "author": "bob",
            "title": "wip",
            "url": "https://github.com/octo/demo/pull/7",
        }
    ]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("open_prs", {"owner": "octo", "limit": 0}),
        ("open_prs", {"owner": "octo", "limit": 101}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "last": 0}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "last": 101}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "preview": 0}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "preview": 1001}),
        ("check_runs", {"repo": REPO, "sha": HEAD, "min_checks": 0}),
        ("check_runs", {"repo": REPO, "sha": HEAD, "min_checks": 101}),
        ("workflow_runs", {"repo": REPO, "sha": HEAD, "limit": 0}),
        ("workflow_runs", {"repo": REPO, "sha": HEAD, "limit": 101}),
    ],
)
async def test_numeric_tool_arguments_are_bounded_before_github_call(wired, name, arguments):
    mcp, patch_client = wired
    _, stub = patch_client({})

    result = await _call(mcp, name, arguments)

    assert result.is_error is True
    assert stub.calls == []


async def test_numeric_tool_schemas_publish_bounds(wired):
    mcp, _ = wired
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    expected = [
        ("open_prs", "limit", 1, 100),
        ("issue_comments_digest", "last", 1, 100),
        ("issue_comments_digest", "preview", 1, 1000),
        ("check_runs", "min_checks", 1, 100),
        ("workflow_runs", "limit", 1, 100),
    ]
    for tool_name, field_name, minimum, maximum in expected:
        schema = tools[tool_name].parameters
        field = schema["properties"][field_name]
        # Optional fields are represented as anyOf by JSON Schema; required fields
        # carry the constraints directly. Check both shapes without depending on
        # the order of anyOf branches.
        candidates = [field, *field.get("anyOf", [])]
        assert any(candidate.get("minimum") == minimum for candidate in candidates)
        assert any(candidate.get("maximum") == maximum for candidate in candidates)


async def test_unexpected_pr_status_response_is_tool_error_and_server_recovers(wired):
    mcp, patch_client = wired
    secret = "unexpected-response-token"
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls/11"): [
                reply(["not", "an", "object"]),
                reply(pr_payload()),
            ]
        },
        token=secret,
    )

    malformed = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    assert malformed.is_error is True
    assert secret not in str(malformed)

    valid = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    assert valid.is_error is False
    assert _data(valid)["ok"] is True


# --- issue_comments_digest ----------------------------------------------------------

async def test_issue_comments_digest_tool_previews_only_and_bounded(wired):
    mcp, patch_client = wired
    items = [comment(i) for i in range(5)]
    items[0]["body"] = "line one\n\nline   two " + "x" * 300
    patch_client(
        {
            ("GET", "/repos/octo/demo/issues/24"): reply({"title": "Roadmap", "state": "open", "body": "b" * 42}),
            ("GET", "/repos/octo/demo/issues/24/comments"): reply(items),
        }
    )
    result = await _call(mcp, "issue_comments_digest", {"repo": REPO, "number": 24, "preview": 20})
    data = _data(result)
    assert data["ok"] is True
    assert data["total_comments"] == 5
    assert len(data["comments"]) == 5
    # Preview rows only: no full body anywhere except explicitly `show`n indexes.
    assert data["shown"] == {}
    for row in data["comments"]:
        assert "body" not in row
        assert len(row["preview"]) <= 21  # 20 + ellipsis
    assert data["comments"][0]["preview"] == "line one line two x…"


async def test_issue_comments_digest_tool_full_body_only_for_requested_indexes(wired):
    mcp, patch_client = wired
    items = [comment(i) for i in range(5)]
    patch_client(
        {
            ("GET", "/repos/octo/demo/issues/24"): reply({"title": "Roadmap", "state": "open", "body": ""}),
            ("GET", "/repos/octo/demo/issues/24/comments"): reply(items),
        }
    )
    result = await _call(mcp, "issue_comments_digest", {"repo": REPO, "number": 24, "show": [2]})
    data = _data(result)
    assert data["shown"] == {"2": "comment 2"} or data["shown"] == {2: "comment 2"}


# --- check_runs -----------------------------------------------------------------------

def check_run(name, conclusion="success", status="completed", annotations=0, run_id=1):
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": HEAD,
        "output": {"annotations_count": annotations},
    }


async def test_check_runs_tool_summarizes_without_waiting(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply(
                {"check_runs": [check_run("unit"), check_run("lint", conclusion="failure", run_id=2)]}
            )
        }
    )
    result = await _call(mcp, "check_runs", {"repo": REPO, "sha": HEAD, "min_checks": 2})
    data = _data(result)
    assert data["total"] == 2
    assert data["failed"] == 1
    assert data["succeeded"] == 1
    assert data["ok"] is False
    assert "failed" in data["reason"]
    assert data["sha"] == HEAD


# --- workflow_runs ----------------------------------------------------------------------

async def test_workflow_runs_tool_by_sha(wired):
    mcp, patch_client = wired
    data_payload = {
        "total_count": 1,
        "workflow_runs": [
            {
                "id": 9,
                "name": "CI",
                "event": "pull_request",
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "html_url": "u",
            }
        ],
    }
    patch_client({("GET", "/repos/octo/demo/actions/runs"): reply(data_payload)})
    result = await _call(mcp, "workflow_runs", {"repo": REPO, "sha": HEAD})
    data = _data(result)
    assert data["ok"] is True
    assert data["runs"][0]["id"] == 9


# --- url_* builders (pure, no network) -----------------------------------------------

async def test_url_pr_tool_default_and_tab(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_pr", {"repo": REPO, "number": 11})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://github.com/octo/demo/pull/11",
        "api": "https://api.github.com/repos/octo/demo/pulls/11",
    }
    result = await _call(mcp, "url_pr", {"repo": REPO, "number": 11, "tab": "checks"})
    data = _data(result)
    assert data == {"ok": True, "web": "https://github.com/octo/demo/pull/11/checks", "api": None}


async def test_url_compare_tool(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_compare", {"repo": REPO, "base": "main", "head": "feature"})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://github.com/octo/demo/compare/main...feature",
        "api": "https://api.github.com/repos/octo/demo/compare/main...feature",
    }


async def test_url_blame_tool_with_line(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_blame", {"repo": REPO, "ref": "main", "path": "a/b.py", "line": 42})
    data = _data(result)
    assert data == {"ok": True, "web": "https://github.com/octo/demo/blame/main/a/b.py#L42", "api": None}


async def test_url_history_tool(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_history", {"repo": REPO, "ref": "main", "path": "a/b.py"})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://github.com/octo/demo/commits/main/a/b.py",
        "api": "https://api.github.com/repos/octo/demo/commits?sha=main&path=a%2Fb.py",
    }


async def test_url_runs_tool_with_filters(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_runs", {"repo": REPO, "branch": "main", "status": "failure"})
    data = _data(result)
    assert data["ok"] is True
    assert data["web"] == "https://github.com/octo/demo/actions?query=branch%3Amain+is%3Afailure"
    assert data["api"] == "https://api.github.com/repos/octo/demo/actions/runs?branch=main&status=failure"


async def test_url_search_tool_pullrequests(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_search", {"query": "fix bug", "kind": "pullrequests"})
    data = _data(result)
    assert data["web"] == "https://github.com/search?q=is%3Apr+fix+bug&type=issues"
    assert data["api"] == "https://api.github.com/search/issues?q=is%3Apr+fix+bug"


async def test_url_raw_tool(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_raw", {"repo": REPO, "ref": "main", "path": "a/b.py"})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://raw.githubusercontent.com/octo/demo/main/a/b.py",
        "api": "https://api.github.com/repos/octo/demo/contents/a/b.py?ref=main",
    }


# --- token handling and error surfacing -----------------------------------------------

async def test_403_is_ok_false_not_a_crash_and_token_is_scrubbed(wired, monkeypatch):
    mcp, patch_client = wired
    secret = "sekrit-token-value"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls/11"): reply(
                {"message": f"forbidden for token {secret}"}, status=403
            )
        },
        token=secret,
    )
    result = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    assert result.is_error is False  # a 403 surfaces as ok: False, not a protocol-level crash
    data = _data(result)
    assert data["ok"] is False
    assert "error" in data
    assert secret not in str(data)
    assert secret not in str(result)


async def test_no_tool_accepts_a_token_argument(wired):
    mcp, _ = wired
    tools = await mcp.list_tools()
    for tool in tools:
        schema = getattr(tool, "parameters", None) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        assert "token" not in properties, f"{tool.name} must not accept a token argument"


async def test_gh_ops_client_reads_token_only_from_environment(monkeypatch):
    """No tool wiring involved: gh_ops.Client() itself must fall back to the env, never a param."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    client = gh_ops.Client()
    assert client._token == "env-token"


# --- public resolver ---------------------------------------------------------------

async def test_public_resolver_tool_can_build_candidates_without_network(wired):
    mcp, _ = wired
    result = await _call(mcp, "resolve_public", {"repo": REPO, "path": "README.md", "probe": False})
    data = _data(result)
    assert data["status"] == "not_checked"
    assert data["auth"] == "anonymous"
    assert data["observations"] == []
    kinds = {item["kind"] for item in data["candidates"]}
    assert {"repository", "api", "pages", "raw", "contents_api"} <= kinds


async def test_public_resolver_invalid_input_is_ok_false(wired):
    mcp, _ = wired
    result = await _call(mcp, "resolve_public", {"repo": "demo", "probe": False})
    assert result.is_error is False
    data = _data(result)
    assert data["ok"] is False
    assert "error" in data


async def test_public_resolver_schema_describes_arguments(wired):
    mcp, _ = wired
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    schema = tools["resolve_public"].parameters["properties"]
    for field in ("repo", "ref", "path", "probe"):
        description = schema[field].get("description")
        if description is None:
            description = next(
                (item.get("description") for item in schema[field].get("anyOf", []) if item.get("description")),
                None,
            )
        assert description


# --- fleet_query: the one non-REST tool; thin over the vendored parent planner's query() ----------

FLEET_DOC = {
    "@context": {"@vocab": "https://example.invalid/fleet#"},
    "generatedAt": "2026-11-09T00:00:00+00:00",
    "parentRevision": "a" * 40,
    "@graph": [
        {"@id": "app/", "@type": "Repository", "repository": "app", "repositoryId": 1, "category": "target",
         "previousNames": ["old-app"], "revision": "b" * 40},
        {"@id": "tool/", "@type": "Repository", "repository": "tool", "repositoryId": 2, "category": "target"},
        {"@id": "edge/1", "@type": "Dependency", "relation": "vd", "source": "tool/tool.py", "target": "app/vendor/tool.py",
         "assertedBy": "app/vendor.lock.json", "pin": "c" * 40},
        {"@id": "fact/FLEET-1", "@type": "Fact", "no": "FLEET-1", "key": "updated|vendor|tool:tool.py|blob →2", "severity": "updated",
         "what": "📥 tool:tool.py", "detail": "blob 1→2 → 1× app (vendor/tool.py)", "repository": ["app/"]},
    ],
}


def _fake_fleet_plan(tmp_path):
    """A stand-in for vendor/vendor_fleet_plan.py exposing the same query(kind, term, graph=, severity=) contract."""
    path = tmp_path / "vendor_fleet_plan.py"
    path.write_text(
        "import json\n"
        "QUERY_KINDS = ('deps', 'who', 'facts', 'why', 'reverify')\n"
        "CALLS = []\n"
        "def query(kind, term='', *, graph='', severity=None, document=None):\n"
        "    CALLS.append((kind, term, graph, severity))\n"
        "    if kind not in QUERY_KINDS:\n"
        "        raise ValueError('kind must be one of deps, who, facts, why, reverify')\n"
        "    doc = json.loads(open(__file__.replace('vendor_fleet_plan.py', 'fleet.jsonld')).read())\n"
        "    if kind == 'who':\n"
        "        rows = [{'repository': g['repository'], 'repository_id': g.get('repositoryId')} for g in doc['@graph']\n"
        "                if g.get('@type') == 'Repository' and (g['repository'] == term or term in g.get('previousNames', []))]\n"
        "    elif kind in ('why', 'reverify'):\n"
        "        rows = [g for g in doc['@graph'] if g.get('@type') == 'Dependency' and g['target'] == term]\n"
        "    elif kind == 'deps':\n"
        "        rows = [g for g in doc['@graph'] if g.get('@type') == 'Dependency' and g['target'].startswith(term + '/')]\n"
        "    else:\n"
        "        rows = [g for g in doc['@graph'] if g.get('@type') == 'Fact' and term in g['detail'] and (severity in (None, g['severity']))]\n"
        "    return {'schema': 'vendor-fleet-query/1', 'kind': kind, 'term': term, 'graph': graph,\n"
        "            'generated_at': doc['generatedAt'], 'parent_revision': doc['parentRevision'], 'rows': rows}\n",
        encoding="utf-8",
    )
    (tmp_path / "fleet.jsonld").write_text(json.dumps(FLEET_DOC), encoding="utf-8")
    return path


async def test_fleet_query_is_listed_and_reports_a_missing_vendored_planner(wired, monkeypatch):
    mcp, _ = wired
    names = {tool.name for tool in await mcp.list_tools()}
    assert "fleet_query" in names
    monkeypatch.delenv("VENDOR_FLEET_PLAN_PY", raising=False)
    import mcp_toolcall_lab.github_ops_server as gos

    monkeypatch.setattr(gos, "FLEET_PLAN_PATH", gos.REPO_ROOT / "nowhere" / "vendor_fleet_plan.py")
    async with FastMCPClient(transport=mcp) as client:
        result = await client.call_tool("fleet_query", {"kind": "who", "term": "app"})
    data = _data(result)
    assert data["ok"] is False and "vendor_fleet_plan.py is missing" in data["error"]


async def test_fleet_query_delegates_to_the_vendored_query_and_never_touches_github(wired, monkeypatch, tmp_path):
    mcp, patch_client = wired
    _client, stub = patch_client({})  # any GitHub call would raise KeyError inside the stub
    monkeypatch.setenv("VENDOR_FLEET_PLAN_PY", str(_fake_fleet_plan(tmp_path)))
    async with FastMCPClient(transport=mcp) as client:
        who = _data(await client.call_tool("fleet_query", {"kind": "who", "term": "old-app"}))
        deps = _data(await client.call_tool("fleet_query", {"kind": "deps", "term": "app", "graph": "https://example.invalid/g.jsonld"}))
        facts = _data(await client.call_tool("fleet_query", {"kind": "facts", "term": "tool.py", "severity": "updated"}))
        none = _data(await client.call_tool("fleet_query", {"kind": "facts", "term": "tool.py", "severity": "failure"}))
        why = _data(await client.call_tool("fleet_query", {"kind": "why", "term": "app/vendor/tool.py"}))
        reverify = _data(await client.call_tool("fleet_query", {"kind": "reverify", "term": "app/vendor/tool.py"}))
    assert who["ok"] and who["rows"][0]["repository_id"] == 1
    assert why["ok"] and why["kind"] == "why" and why["rows"][0]["source"] == "tool/tool.py"
    assert reverify["ok"] and reverify["kind"] == "reverify" and reverify["rows"][0]["target"] == "app/vendor/tool.py"
    assert deps["ok"] and deps["rows"][0]["target"] == "app/vendor/tool.py" and deps["graph"] == "https://example.invalid/g.jsonld"
    assert facts["ok"] and facts["rows"][0]["no"] == "FLEET-1"
    assert none["ok"] is True and none["matched"] is False and none["rows"] == []
    assert stub.calls == []


async def test_fleet_query_schema_bounds_kind_severity_and_https_graph(wired):
    mcp, _ = wired
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    properties = (getattr(tools["fleet_query"], "parameters", None) or {}).get("properties", {})
    assert properties["kind"]["pattern"] == "^(deps|who|facts|why|reverify)$"
    assert "token" not in properties
    assert properties["graph"]["pattern"] == "^https://"
    async with FastMCPClient(transport=mcp) as client:
        bad = await client.call_tool("fleet_query", {"kind": "drop", "term": "x"}, raise_on_error=False)
    assert bad.is_error


async def test_fleet_query_uses_real_vendored_planner_for_a_valid_empty_graph(wired, monkeypatch):
    """No matches is a successful query, not an MCP transport/planner error."""
    mcp, _ = wired
    import mcp_toolcall_lab.github_ops_server as gos
    monkeypatch.delenv("VENDOR_FLEET_PLAN_PY", raising=False)
    planner = gos.load_fleet_plan()
    monkeypatch.setattr(planner, "load_graph", lambda *_a, **_kw: FLEET_DOC)
    monkeypatch.setattr(gos, "load_fleet_plan", lambda: planner)
    async with FastMCPClient(transport=mcp) as client:
        result = _data(await client.call_tool("fleet_query", {"kind": "facts", "term": "not-present"}))
    assert result["ok"] is True
    assert result["matched"] is False
    assert result["rows"] == []
    assert result["parent_revision"] == FLEET_DOC["parentRevision"]


@pytest.mark.parametrize("failure", ["fetch", "json", "schema"])
async def test_fleet_query_real_planner_errors_and_recovery(wired, monkeypatch, failure):
    from io import BytesIO
    from urllib.error import URLError
    import urllib.request
    import mcp_toolcall_lab.github_ops_server as gos

    mcp, _ = wired
    monkeypatch.delenv("VENDOR_FLEET_PLAN_PY", raising=False)
    planner = gos.load_fleet_plan()
    monkeypatch.setattr(gos, "load_fleet_plan", lambda: planner)

    def response(*args, **kwargs):
        if failure == "fetch":
            raise URLError("fixture fetch failed")
        return BytesIO(b"not-json" if failure == "json" else b'{}')

    monkeypatch.setattr(urllib.request, "urlopen", response)
    async with FastMCPClient(transport=mcp) as client:
        failed = _data(await client.call_tool("fleet_query", {"kind": "facts", "term": "absent"}))
        assert failed["ok"] is False
        assert failed["error"]
        assert "matched" not in failed
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: BytesIO(json.dumps(FLEET_DOC).encode()))
        recovered = _data(await client.call_tool("fleet_query", {"kind": "facts", "term": "absent"}))
        assert recovered["ok"] is True
        assert recovered["matched"] is False
        assert recovered["rows"] == []


# ═══ · ironmate client ═══════════════════════════════════════════════════════════════════════════════════

FIXTURE = Path(__file__).parents[1] / "fixtures" / "ironmate" / "catalog.json"


class FakeSession:
    def __init__(self, tools):
        self.tools = tools
        self.methods = []

    def initialize(self):
        self.methods.append("initialize")
        return {"result": {}}

    def list_tools(self):
        self.methods.append("tools/list")
        return {"result": {"tools": self.tools}}

    def call_tool(self, name, arguments, **kwargs):
        self.methods.append("tools/call")
        return {"result": {"content": [{"type": "text", "text": f"{name}:{arguments['query']}"}]}}


def _snapshot_tools():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["tools"]


def test_discovery_matches_offline_snapshot():
    expected = _snapshot_tools()
    session = FakeSession(list(reversed(expected)))
    assert IronmateClient(session).discover() == expected
    assert session.methods == ["initialize", "tools/list"]


def test_representative_call_is_delegated_without_business_logic():
    session = FakeSession(_snapshot_tools())
    result = IronmateClient(session).call("search_repository_metadata", {"query": "markdown"})
    assert session.methods == ["tools/call"]
    assert result["result"]["content"][0]["text"] == "search_repository_metadata:markdown"


def test_catalog_rejects_missing_tools():
    with pytest.raises(ValueError, match="result.tools"):
        normalize_catalog({"result": {}})


def test_normalize_catalog_sorts_multiple_tools_and_preserves_output_schema():
    response = {
        "result": {
            "tools": [
                {
                    "name": "zeta",
                    "description": "Z",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                },
                {
                    "name": "alpha",
                    "description": "A",
                    "inputSchema": {"type": "object"},
                },
            ]
        }
    }
    tools = normalize_catalog(response)
    assert [tool["name"] for tool in tools] == ["alpha", "zeta"]
    assert tools[1]["outputSchema"]["properties"]["ok"]["type"] == "boolean"
    assert "outputSchema" not in tools[0]


@pytest.mark.parametrize("bad_result", [None, "bad", [], 1])
def test_catalog_rejects_non_object_result(bad_result):
    with pytest.raises(ValueError, match="result.tools"):
        normalize_catalog({"result": bad_result})


# ═══ · ironmate trace ════════════════════════════════════════════════════════════════════════════════════

class FakeSession_ironmate_trace:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = []

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return {"result": {"tools": []}}

    def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if self.error:
            raise self.error
        return {"result": {"content": [{"type": "text", "text": "ok"}]}}


def test_representative_ironmate_call_records_shared_trace(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    session = FakeSession_ironmate_trace()
    client = IronmateClient(session)

    result = client.call(
        "search_repository_metadata",
        {"query": "markdown"},
        meta={"source": "jev-router", "trace_id": "trace_ironmate_1"},
        debug={"chat_id": "chat_ironmate_1"},
    )

    assert result["result"]["content"][0]["text"] == "ok"
    assert session.calls[0][2]["meta"]["source"] == "jev-router"
    [row] = record_read_jsonl(str(log))
    assert row["event"] == "ironmate-client/tools/call"
    assert row["tool"] == "search_repository_metadata"
    assert row["arguments"] == {"query": "markdown"}
    assert row["outcome"] == "success"
    assert row["meta"]["trace_id"] == "trace_ironmate_1"
    assert row["meta"]["source"] == "jev-router"
    assert row["chat_id"] == "chat_ironmate_1"
    assert row["duration_ms"] >= 0


def test_failed_ironmate_call_records_error_then_reraises(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    client = IronmateClient(FakeSession_ironmate_trace(error=RuntimeError("ironmate unavailable")))

    with pytest.raises(RuntimeError, match="ironmate unavailable"):
        client.call("search_repository_metadata", {"query": "markdown"})

    [row] = record_read_jsonl(str(log))
    assert row["event"] == "ironmate-client/tools/call"
    assert row["meta"]["source"] == "ironmate-client"
    assert row["outcome"] == "error"
    assert row["error"] == "ironmate unavailable"
    assert row["duration_ms"] >= 0


def test_caller_trace_is_not_counted_as_server_tool_call(tmp_path, monkeypatch):
    from mcp_toolcall_lab.record import mcp_tool_calls

    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    IronmateClient(FakeSession_ironmate_trace()).call("search_repository_metadata", {"query": "markdown"})

    rows = record_read_jsonl(str(log))
    assert rows[0]["event"] == "ironmate-client/tools/call"
    assert mcp_tool_calls(rows) == []


# ═══ · prompt experiment ═════════════════════════════════════════════════════════════════════════════════
# Offline prompt/model experiment fixtures — no sockets, no API keys.

PROMPT = ROOT / "system_prompts" / "strict_tool_selection.md"


def _cases() -> dict[str, dict]:
    return {case["id"]: case for case in iter_cases()}


def _hops_by_method(case: dict) -> dict[str, dict]:
    return {hop_method(hop): hop for hop in iter_mcp_hops(case)}


def test_shipped_fixtures_are_the_two_agreed_cases() -> None:
    ids = {path.stem for path in DEFAULT_FIXTURE_DIR.glob("*.json")}
    assert ids == {"available_tool_success", "fictional_tool_reject"}


def test_fixtures_use_official_openai_tools_and_tool_calls() -> None:
    prompt = PROMPT.read_text(encoding="utf-8").strip()
    for case in iter_cases():
        request = case["openai"]["request"]
        completion = case["openai"]["completion"]
        assert case["system_prompt"] == "system_prompts/strict_tool_selection.md"
        assert case["_system_prompt_text"] == prompt
        assert request["messages"][0]["role"] == "system"
        assert request["messages"][0]["content"] == prompt
        assert request["tool_choice"] == "auto"
        assert "reasoning" in case["settings"]
        assert "stop" in case["settings"]
        assert request.get("stop") == case["settings"]["stop"]
        for tool in request["tools"]:
            assert tool["type"] == "function"
            assert "function" in tool
            assert tool["function"]["name"] in AVAILABLE_TOOLS
            assert tool["function"]["description"] == TOOL_DESCRIPTIONS[tool["function"]["name"]]
            assert tool["function"]["parameters"]["type"] == "object"
        message = completion["choices"][0]["message"]
        assert message["role"] == "assistant"
        for call in message.get("tool_calls") or []:
            assert call["type"] == "function"
            assert call["id"].startswith("call_")
            assert isinstance(call["function"]["arguments"], str)
            json.loads(call["function"]["arguments"])


def test_mcp_hops_are_streamable_http_jsonrpc_send_and_return() -> None:
    """Fixtures record POST /mcp JSON-RPC envelopes, not lab-normalized event rows."""
    for case in iter_cases():
        hops = iter_mcp_hops(case)
        assert hop_method(hops[0]) == MCP_INITIALIZE
        methods = [hop_method(hop) for hop in hops]
        assert MCP_INITIALIZED in methods
        assert MCP_TOOLS_LIST in methods
        for hop in hops:
            assert "event" not in hop
            http = hop["http"]
            assert http["method"] == "POST"
            assert http["path"] == "/mcp"
            request = hop_request(hop)
            assert request["jsonrpc"] == "2.0"
            assert request["method"]
            if hop_method(hop) == MCP_INITIALIZED:
                assert http["status"] == 202
                assert http["response"] is None
                assert (http.get("response_sse") or "") == ""
                continue
            assert http["status"] == 200
            assert http["response_headers"]["Content-Type"] == "text/event-stream"
            parsed = extract_sse_data(http["response_sse"])
            assert parsed == hop_response(hop)
            assert parsed["jsonrpc"] == "2.0"
            assert parsed["id"] == request["id"]
            assert "result" in parsed


def test_initialize_and_tools_list_round_trips() -> None:
    for case in iter_cases():
        hops = _hops_by_method(case)
        init = hops[MCP_INITIALIZE]
        request = hop_request(init)
        result = hop_response(init)["result"]
        assert request["method"] == MCP_INITIALIZE
        assert request["params"]["protocolVersion"] == "2025-06-18"
        assert result["protocolVersion"] == "2025-06-18"
        assert result["serverInfo"]["name"] == "mcp-toolcall-lab"
        assert response_header(init, "Mcp-Session-Id") == case["ui"]["mcp_session_id"]

        listed = hops[MCP_TOOLS_LIST]
        assert hop_request(listed)["method"] == MCP_TOOLS_LIST
        assert request_header(listed, "Mcp-Session-Id") == case["ui"]["mcp_session_id"]
        tools = hop_response(listed)["result"]["tools"]
        specs = [
            {"name": tool["name"], "description": tool["description"], "inputSchema": tool["inputSchema"]}
            for tool in sorted(tools, key=lambda item: item["name"])
        ]
        # Recorded prompt fixtures preserve the catalog seen at capture time.
        # Validate those schemas against today's source of truth without requiring
        # later-added tools to appear retroactively in historical wire captures.
        expected_by_name = {spec["name"]: spec for spec in EXPECTED_TOOL_SPECS}
        assert specs == [expected_by_name[spec["name"]] for spec in specs]
        assert all(spec["name"] in expected_by_name for spec in specs)


def test_available_tool_success_tools_call_send_and_return() -> None:
    case = _cases()["available_tool_success"]
    hop = _hops_by_method(case)[MCP_TOOLS_CALL]
    request = hop_request(hop)
    result = hop_response(hop)["result"]
    params = request["params"]
    ui = case["ui"]
    assert request["method"] == MCP_TOOLS_CALL
    assert params["name"] == "find_municipalities"
    assert params["arguments"] == {"query": "Exampleville"}
    assert params["_meta"]["chat_id"] == ui["chat_id"]
    assert params["_meta"]["call_id"] == ui["tool_call_id"]
    assert request_header(hop, "X-Chat-Id") == ui["chat_id"]
    assert request_header(hop, "X-OpenWebUI-Message-Id") == ui["message_id"]
    assert request_header(hop, "Mcp-Session-Id") == ui["mcp_session_id"]
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == [
        {"code": "90001", "name": "Exampleville", "prefecture": "Example Region"}
    ]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]["result"]


def test_ui_ids_bind_chat_message_tool_call_and_tool_result() -> None:
    """chat_id → UI message / tool-call id → MCP tools/call → role:tool return."""
    case = _cases()["available_tool_success"]
    ui = case["ui"]
    call_id = ui["tool_call_id"]
    openai_call = case["openai"]["completion"]["choices"][0]["message"]["tool_calls"][0]
    assert openai_call["id"] == call_id
    hop = _hops_by_method(case)[MCP_TOOLS_CALL]
    assert hop_request(hop)["params"]["_meta"]["call_id"] == call_id
    assert hop_request(hop)["params"]["_meta"]["chat_id"] == ui["chat_id"]
    returned = tool_result_messages(case["openai"])
    assert len(returned) == 1
    assert returned[0]["tool_call_id"] == call_id
    assert returned[0]["content"] == hop_response(hop)["result"]["content"][0]["text"]
    follow = case["openai"]["followup"]["completion"]["choices"][0]
    assert follow["finish_reason"] == "stop"
    assert follow["message"]["role"] == "assistant"


def test_available_tool_success_replays_pass() -> None:
    audit = replay_case(_cases()["available_tool_success"])
    assert audit["verdict"] == VERDICT_PASS
    assert audit["matched_expected"] is True
    assert audit["selected_tools"] == ["find_municipalities"]
    assert audit["fictional_tools"] == []
    assert audit["mcp_called"] == ["find_municipalities"]
    assert audit["finish_reason"] == "tool_calls"
    assert audit["raw_schema_valid"] is True
    assert audit["server_accepted"] is True
    assert audit["outcome"] == "success"
    assert mcp_call_hops(iter_mcp_hops(_cases()["available_tool_success"]))


def test_fictional_tool_reject_has_no_sent_tools_call() -> None:
    case = _cases()["fictional_tool_reject"]
    user = case["openai"]["request"]["messages"][1]["content"]
    assert "query_unadvertised_tool" in user
    assert case["ui"]["tool_call_id"] is None
    assert not mcp_call_hops(iter_mcp_hops(case, sent_only=True))
    assert not tool_result_messages(case["openai"])
    completion = case["openai"]["completion"]["choices"][0]
    assert completion["finish_reason"] == "stop"
    assert not completion["message"].get("tool_calls")
    audit = replay_case(case)
    assert audit["verdict"] == VERDICT_PASS
    assert audit["matched_expected"] is True
    assert audit["selected_tools"] == []
    assert audit["fictional_tools"] == []
    assert audit["mcp_called"] == []
    assert audit["raw_schema_valid"] is None


def test_fictional_tool_reject_records_server_round_trip() -> None:
    """If query_unadvertised_tool is sent, MCP returns isError — recorded, not conversation path."""
    case = _cases()["fictional_tool_reject"]
    hop = case["mcp_rejected_call"]
    request = hop_request(hop)
    result = hop_response(hop)["result"]
    ui = case["ui"]
    assert hop_was_sent(hop) is True
    assert hop not in iter_mcp_hops(case)
    assert request["jsonrpc"] == "2.0"
    assert request["method"] == MCP_TOOLS_CALL
    assert request["params"]["name"] == "query_unadvertised_tool"
    assert request_header(hop, "X-Chat-Id") == ui["chat_id"]
    assert request_header(hop, "X-OpenWebUI-Message-Id") == ui["message_id"]
    assert request_header(hop, "Mcp-Session-Id") == ui["mcp_session_id"]
    assert hop["http"]["status"] == 200
    parsed = extract_sse_data(hop["http"]["response_sse"])
    assert parsed == hop_response(hop)
    assert result["isError"] is True
    assert "query_unadvertised_tool" in result["content"][0]["text"]


def test_invented_tool_call_is_fail() -> None:
    case = copy.deepcopy(_cases()["fictional_tool_reject"])
    case["openai"]["completion"]["choices"][0] = {
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_invented",
                    "type": "function",
                    "function": {"name": "query_unadvertised_tool", "arguments": "{}"},
                }
            ],
        },
    }
    case["mcp"].append(case["mcp_rejected_call"])
    case["expected"] = {
        "verdict": VERDICT_FAIL,
        "selected_tools": ["query_unadvertised_tool"],
        "fictional_tools": ["query_unadvertised_tool"],
    }
    audit = replay_case(case)
    assert audit["verdict"] == VERDICT_FAIL
    assert audit["fictional_tools"] == ["query_unadvertised_tool"]
    assert audit["mcp_called"] == ["query_unadvertised_tool"]
    assert audit["server_accepted"] is False
    assert audit["outcome"] == "error"
    assert audit["matched_expected"] is True


def test_audit_jsonl_is_comparison_fields_only(tmp_path: Path) -> None:
    audits = replay_fixtures()
    path = tmp_path / "prompt-experiments.jsonl"
    write_audit(path, audits)
    text = path.read_text(encoding="utf-8")
    assert "Exampleville" not in text
    assert "query_unadvertised_tool" not in text
    assert "live provider" not in text
    assert "arguments" not in text
    assert "api_key" not in text
    assert "jsonrpc" not in text
    assert "Mcp-Session-Id" not in text
    assert "event: message" not in text
    rows = [json.loads(line) for line in text.splitlines() if line]
    assert {row["id"] for row in rows} == {"available_tool_success", "fictional_tool_reject"}
    for row in rows:
        assert set(row) == set(AUDIT_KEYS)
        assert row["matched_expected"] is True


def test_cli_replay_writes_audit_and_exits_zero(tmp_path: Path, capsys) -> None:
    out = tmp_path / "audit.jsonl"
    assert main_prompt_experiment(["replay", "--fixtures", str(DEFAULT_FIXTURE_DIR), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert len(printed) == 2
    assert all(row["matched_expected"] for row in printed)
    assert out.is_file()


def test_replay_does_not_open_a_socket(monkeypatch) -> None:
    import urllib.request

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prompt experiments must stay offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    audits = replay_fixtures()
    assert {row["id"] for row in audits} == {"available_tool_success", "fictional_tool_reject"}


def test_load_case_resolves_system_prompt_path() -> None:
    path = DEFAULT_FIXTURE_DIR / "available_tool_success.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["openai"]["request"]["messages"][0]["content"] == raw["system_prompt"]
    assert raw["openai"]["followup"]["request"]["messages"][0]["content"] == raw["system_prompt"]
    loaded = load_case(path)
    prompt = PROMPT.read_text(encoding="utf-8").strip()
    assert loaded["openai"]["request"]["messages"][0]["content"] == prompt
    assert loaded["openai"]["followup"]["request"]["messages"][0]["content"] == prompt
