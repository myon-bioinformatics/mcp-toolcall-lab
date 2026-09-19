"""OpenAI + MCP JSONL loop helpers — no Docker, no live products."""

from __future__ import annotations

import pytest

from tests.real_chat_ui.openwebui_loop import (
    assert_openwebui_tool_loop,
    lab_vs_product_chat_ids,
    product_conversation_id,
    summarize_mcp_handshake,
    summarize_openai_tool_loop,
)

CALL = "call_" + "a" * 24
CHATCMPL_1 = "chatcmpl-turn1"
CHATCMPL_2 = "chatcmpl-turn2"
OWUI_CHAT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_openai_loop_joins_tool_call_id_across_the_followup() -> None:
    events = [
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [CALL],
            "inbound_call_ids": [],
            "has_tool_result": False,
            "completion_id": CHATCMPL_1,
            "wire_messages": [{"role": "user"}],
            "wire_assistant": {
                "role": "assistant",
                "tool_calls": [{"id": CALL, "type": "function", "name": "lab_find_municipalities"}],
            },
        },
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [],
            "inbound_call_ids": [CALL],
            "has_tool_result": True,
            "completion_id": CHATCMPL_2,
            "wire_messages": [
                {"role": "user"},
                {"role": "assistant", "tool_calls": [{"id": CALL}]},
                {"role": "tool", "tool_call_id": CALL},
            ],
            "wire_assistant": {"role": "assistant"},
        },
    ]
    loop = summarize_openai_tool_loop(events)
    assert loop["completion_n"] == 2
    assert loop["call_ids"] == [CALL]
    assert loop["follow_inbound_call_ids"] == [CALL]
    assert loop["follow_tool_message_ids"] == [CALL]
    assert loop["has_tool_result_followup"] is True
    assert loop["final_is_assistant"] is True


def test_mcp_handshake_reads_wire_method_names() -> None:
    events = [
        {"event": "initialize", "debug": {"session_id": "sess-1", "request_id": "req-init"}},
        {"event": "notifications/initialized", "debug": {"session_id": "sess-1"}},
        {"event": "tools/list", "debug": {"session_id": "sess-1", "request_id": "req-list"}},
        {
            "event": "tools/call",
            "tool": "find_municipalities",
            "debug": {"session_id": "sess-1", "request_id": "req-call", "chat_id": OWUI_CHAT},
            "chat_id": OWUI_CHAT,
        },
    ]
    handshake = summarize_mcp_handshake(events)
    assert handshake["initialize"] is True
    assert handshake["tools_list"] is True
    assert handshake["tools_call"] is True
    assert handshake["tool_names"] == ["find_municipalities"]
    assert handshake["session_ids"] == ["sess-1"]
    assert "req-init" in handshake["request_ids"]


def test_lab_chat_star_is_not_the_product_conversation_id() -> None:
    assert product_conversation_id(f"http://127.0.0.1:3000/c/{OWUI_CHAT}") == OWUI_CHAT
    split = lab_vs_product_chat_ids(
        [
            {"chat_id": "chat_" + "f" * 24},
            {"chat_id": OWUI_CHAT},
        ]
    )
    assert split["lab_chat_ids"] == ["chat_" + "f" * 24]
    assert split["product_chat_ids"] == [OWUI_CHAT]


def test_assert_loop_accepts_a_complete_fixture_and_rejects_a_short_one() -> None:
    openai = [
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [CALL],
            "inbound_call_ids": [],
            "has_tool_result": False,
            "completion_id": CHATCMPL_1,
            "chat_id": OWUI_CHAT,
            "wire_messages": [{"role": "user"}],
            "wire_assistant": {"role": "assistant", "tool_calls": [{"id": CALL}]},
        },
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [],
            "inbound_call_ids": [CALL],
            "has_tool_result": True,
            "completion_id": CHATCMPL_2,
            "chat_id": OWUI_CHAT,
            "wire_messages": [{"role": "tool", "tool_call_id": CALL}],
            "wire_assistant": {"role": "assistant"},
        },
    ]
    mcp = [
        {
            "event": "initialize",
            "debug": {"session_id": "sess-1", "request_id": "1-init", "chat_id": OWUI_CHAT},
        },
        {
            "event": "tools/list",
            "debug": {"session_id": "sess-1", "request_id": "2-list", "chat_id": OWUI_CHAT},
        },
        {
            "event": "tools/call",
            "tool": "find_municipalities",
            "chat_id": OWUI_CHAT,
            "debug": {"session_id": "sess-1", "request_id": "3-call", "chat_id": OWUI_CHAT},
        },
    ]
    result = assert_openwebui_tool_loop(
        openai_events=openai,
        mcp_events=mcp,
        page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
        ui_text="Yokohama (code 14109) is in Kanagawa.",
    )
    assert result["conversation_id"] == OWUI_CHAT
    assert result["ids"]["product_chat_ids"] == [OWUI_CHAT]

    with pytest.raises(AssertionError):
        assert_openwebui_tool_loop(
            openai_events=openai[:1],
            mcp_events=mcp,
            page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
            ui_text="Yokohama",
        )
