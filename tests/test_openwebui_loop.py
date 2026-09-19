"""OpenAI + MCP JSONL loop helpers — no Docker, no live products."""

from __future__ import annotations

import pytest

from tests.real_chat_ui.openwebui_loop import (
    assert_openwebui_tool_loop,
    is_background_task,
    is_product_ui_message_id,
    lab_vs_product_chat_ids,
    message_ids_from_owui_chat,
    mcp_tools_call_message_ids,
    product_conversation_id,
    summarize_mcp_handshake,
    summarize_openai_tool_loop,
)

CALL = "call_" + "a" * 24
CHATCMPL_1 = "chatcmpl-turn1"
CHATCMPL_2 = "chatcmpl-turn2"
OWUI_CHAT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OWUI_MSG = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


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


def test_summarize_picks_the_last_complete_tool_loop() -> None:
    call_old = "call_" + "c" * 24
    call_new = CALL
    events = [
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [call_old],
            "inbound_call_ids": [],
            "has_tool_result": False,
            "completion_id": "chatcmpl-old1",
            "wire_assistant": {"role": "assistant", "tool_calls": [{"id": call_old}]},
        },
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [],
            "inbound_call_ids": [call_old],
            "has_tool_result": True,
            "completion_id": "chatcmpl-old2",
            "wire_messages": [{"role": "tool", "tool_call_id": call_old}],
            "wire_assistant": {"role": "assistant"},
        },
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [call_new],
            "inbound_call_ids": [],
            "has_tool_result": False,
            "completion_id": CHATCMPL_1,
            "wire_assistant": {"role": "assistant", "tool_calls": [{"id": call_new}]},
        },
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [],
            "inbound_call_ids": [call_new],
            "has_tool_result": True,
            "completion_id": CHATCMPL_2,
            "wire_messages": [{"role": "tool", "tool_call_id": call_new}],
            "wire_assistant": {"role": "assistant"},
        },
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": ["call_" + "d" * 24],
            "inbound_call_ids": [],
            "has_tool_result": False,
            "completion_id": "chatcmpl-orphan",
            "wire_assistant": {"role": "assistant", "tool_calls": [{"id": "call_" + "d" * 24}]},
        },
    ]
    loop = summarize_openai_tool_loop(events)
    assert loop["first_completion_id"] == CHATCMPL_1
    assert loop["follow_completion_id"] == CHATCMPL_2
    assert loop["call_ids"] == [call_new]


def test_role_tool_followup_requires_matching_tool_call_id() -> None:
    openai = _openai_tool_loop_rows()
    openai[1]["wire_messages"] = [{"role": "tool", "tool_call_id": "call_other"}]
    openai[1]["inbound_call_ids"] = [CALL]
    with pytest.raises(AssertionError, match="tool_call_id"):
        assert_openwebui_tool_loop(
            openai_events=openai,
            mcp_events=_mcp_handshake_rows(),
            page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
            ui_text="Yokohama",
            ui_message_ids=[OWUI_MSG],
        )


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


def test_product_conversation_id_falls_back_to_logs_when_url_stays_on_root() -> None:
    assert product_conversation_id("http://127.0.0.1:3000/") is None
    assert (
        product_conversation_id(
            "http://127.0.0.1:3000/",
            [{"kind": "chat.completions", "chat_id": OWUI_CHAT}],
        )
        == OWUI_CHAT
    )


def _openai_tool_loop_rows() -> list[dict]:
    return [
        {
            "kind": "chat.completions",
            "tool_names": ["lab_find_municipalities"],
            "call_ids": [CALL],
            "inbound_call_ids": [],
            "has_tool_result": False,
            "completion_id": CHATCMPL_1,
            "chat_id": OWUI_CHAT,
            "user": "Find municipalities named Yokohama",
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
            "chat_id": OWUI_CHAT,
            "user": "Find municipalities named Yokohama",
            "wire_messages": [
                {"role": "user"},
                {"role": "assistant", "tool_calls": [{"id": CALL}]},
                {"role": "tool", "tool_call_id": CALL},
            ],
            "wire_assistant": {"role": "assistant"},
        },
    ]


def _mcp_handshake_rows() -> list[dict]:
    return [
        {
            "event": "initialize",
            "debug": {"session_id": "sess-1", "request_id": "1-init", "chat_id": OWUI_CHAT, "message_id": OWUI_MSG},
        },
        {
            "event": "tools/list",
            "debug": {"session_id": "sess-1", "request_id": "2-list", "chat_id": OWUI_CHAT, "message_id": OWUI_MSG},
        },
        {
            "event": "tools/call",
            "tool": "find_municipalities",
            "chat_id": OWUI_CHAT,
            "debug": {
                "session_id": "sess-1",
                "request_id": "3-call",
                "chat_id": OWUI_CHAT,
                "message_id": OWUI_MSG,
            },
        },
    ]


def test_title_generation_is_not_the_chat_turn() -> None:
    """Open WebUI background jobs start with ``### Task:`` and carry no tools."""
    title = {
        "kind": "chat.completions",
        "user": "### Task: Generate a concise chat title",
        "tool_names": [],
        "call_ids": [],
        "inbound_call_ids": [],
        "has_tool_result": False,
        "completion_id": "chatcmpl-title",
        "chat_id": OWUI_CHAT,
        "wire_messages": [{"role": "user"}],
        "wire_assistant": {"role": "assistant"},
    }
    assert is_background_task(title) is True
    events = [title, *_openai_tool_loop_rows()]
    loop = summarize_openai_tool_loop(events)
    assert loop["first_completion_id"] == CHATCMPL_1
    assert loop["follow_completion_id"] == CHATCMPL_2
    assert loop["tool_names"] == ["lab_find_municipalities"]
    assert loop["call_ids"] == [CALL]
    assert loop["chat_id"] == OWUI_CHAT

    result = assert_openwebui_tool_loop(
        openai_events=events,
        mcp_events=_mcp_handshake_rows(),
        page_url="http://127.0.0.1:3000/",
        ui_text="Yokohama (code 14109) is in Kanagawa.",
        ui_message_ids=[OWUI_MSG],
    )
    assert result["conversation_id"] == OWUI_CHAT
    assert result["ui_message_id"] == OWUI_MSG


def test_assert_loop_accepts_a_complete_fixture_and_rejects_a_short_one() -> None:
    openai = _openai_tool_loop_rows()
    mcp = _mcp_handshake_rows()
    result = assert_openwebui_tool_loop(
        openai_events=openai,
        mcp_events=mcp,
        page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
        ui_text="Yokohama (code 14109) is in Kanagawa.",
        ui_message_ids=[OWUI_MSG],
    )
    assert result["conversation_id"] == OWUI_CHAT
    assert result["ids"]["product_chat_ids"] == [OWUI_CHAT]
    assert result["ui_message_id"] == OWUI_MSG

    with pytest.raises(AssertionError):
        assert_openwebui_tool_loop(
            openai_events=openai[:1],
            mcp_events=mcp,
            page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
            ui_text="Yokohama",
            ui_message_ids=[OWUI_MSG],
        )


def test_mcp_message_id_must_be_the_product_ui_message() -> None:
    openai = _openai_tool_loop_rows()
    mcp = _mcp_handshake_rows()
    payload = {
        "id": OWUI_CHAT,
        "chat": {
            "history": {
                "messages": {
                    OWUI_MSG: {"id": OWUI_MSG, "role": "user", "content": "Find municipalities named Yokohama"},
                    "assistant-uuid": {"id": "assistant-uuid", "role": "assistant"},
                },
                "currentId": "assistant-uuid",
            }
        },
    }
    assert OWUI_MSG in message_ids_from_owui_chat(payload)
    assert mcp_tools_call_message_ids(mcp) == [OWUI_MSG]
    assert is_product_ui_message_id(OWUI_MSG) is True
    assert is_product_ui_message_id("{{MESSAGE_ID}}") is False
    assert is_product_ui_message_id("chat_" + "f" * 24) is False
    assert is_product_ui_message_id(CALL) is False

    with pytest.raises(AssertionError, match="X-OpenWebUI-Message-Id"):
        assert_openwebui_tool_loop(
            openai_events=openai,
            mcp_events=[{**row, "debug": {k: v for k, v in (row.get("debug") or {}).items() if k != "message_id"}} for row in mcp],
            page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
            ui_text="Yokohama",
            ui_message_ids=[OWUI_MSG],
        )
    with pytest.raises(AssertionError, match="harvest"):
        assert_openwebui_tool_loop(
            openai_events=openai,
            mcp_events=mcp,
            page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
            ui_text="Yokohama",
            ui_message_ids=[],
        )
    with pytest.raises(AssertionError):
        assert_openwebui_tool_loop(
            openai_events=openai,
            mcp_events=mcp,
            page_url=f"http://127.0.0.1:3000/c/{OWUI_CHAT}",
            ui_text="Yokohama",
            ui_message_ids=["cccccccccccccccc-dddd-eeee-ffff-000000000000"],
        )
