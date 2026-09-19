from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp_toolcall_lab.mock.common import close_http11_sse, new_call_id

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
if str(DEMOS) not in sys.path:
    sys.path.insert(0, str(DEMOS))

import openai_toolcall_mock as mock  # noqa: E402


def test_no_tools_is_plain_text_for_picker_off() -> None:
    message = mock.decide_assistant_message(
        {"messages": [{"role": "user", "content": "Find municipalities named Yokohama"}]}
    )
    assert message.get("tool_calls") is None
    assert "No MCP tools were attached" in message["content"]


def test_librechat_prefixed_tool_name_emits_tool_call() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
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
    assert "Yokohama" in message["tool_calls"][0]["function"]["arguments"]


def test_tool_result_turn_returns_yokohama() -> None:
    message = mock.decide_assistant_message(
        {
            "messages": [
                {"role": "user", "content": "Find municipalities named Yokohama"},
                {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
                {"role": "tool", "tool_call_id": "call_1", "content": '[{"name":"Yokohama"}]'},
            ]
        }
    )
    assert "Yokohama" in message["content"]
    assert message.get("tool_calls") is None


def test_completion_and_call_ids_are_prefixed() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
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
        "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
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
            {"role": "user", "content": "Find municipalities named Yokohama"},
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
