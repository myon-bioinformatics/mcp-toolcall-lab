"""MCP log correlation: chat_id from meta, headers, session, or mint."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mcp_toolcall_lab.chat_sim import new_call_id as sim_call_id
from mcp_toolcall_lab.chat_sim import new_chat_id as sim_chat_id
from mcp_toolcall_lab.record import (
    chat_id_from_headers,
    new_call_id,
    new_chat_id,
    record_call,
    resolve_correlation,
)
from tests.test_httpx_protocol import ACCEPT, McpHttpxSession
from tests.test_streamable_http_protocol import running_mcp_server


def test_id_mints_live_in_one_place() -> None:
    assert sim_chat_id is new_chat_id
    assert sim_call_id is new_call_id
    assert new_chat_id().startswith("chat_")
    assert new_call_id().startswith("call_")


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


def test_record_call_keeps_meta_and_adds_debug(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    record_call(
        tool="find_municipalities",
        arguments={"query": "Yokohama"},
        outcome="success",
        result=[{"name": "Yokohama"}],
        meta={"chat_id": "chat_wire", "source": "stub-front"},
        debug={"chat_id": "chat_wire", "chat_id_source": "meta"},
        duration_ms=1.5,
    )
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["event"] == "tools/call"
    assert row["chat_id"] == "chat_wire"
    assert row["meta"] == {"chat_id": "chat_wire", "source": "stub-front"}
    assert row["debug"]["chat_id_source"] == "meta"
    assert row["debug"]["result_n"] == 1
    assert row["duration_ms"] == 1.5
    monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)
    assert os.environ.get("MCP_TOOLCALL_LOG") is None


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
                        "arguments": {"query": "Yokohama"},
                    },
                },
            )
            session.client.post(
                session.url,
                headers={k: v for k, v in headers.items() if k.lower() != "x-chat-id"},
                json={
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "find_stations",
                        "arguments": {"municipality_code": "14109"},
                    },
                },
            )
            session.close()
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert events[0]["debug"]["chat_id_source"] == "header"
        assert events[0]["chat_id"] == "chat_from_ui"
        assert events[1]["chat_id"] == "chat_from_ui"
        assert events[1]["debug"]["chat_id_source"] == "session"
