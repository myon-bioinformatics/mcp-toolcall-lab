"""Cross-runtime contract checks for the deterministic Deno chat stub.

The Deno handler and this test deliberately consume the existing official
OpenAI tools/tool_calls and MCP Streamable HTTP fixtures. No socket, model,
API key, or product-specific wire is added here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "prompt_experiments"
EXPECTED_METHODS = [
    "initialize",
    "notifications/initialized",
    "tools/list",
    "tools/call",
]


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_deno_and_python_use_the_same_fixture_ids() -> None:
    source = (ROOT / "deploy" / "chat-stub" / "main.ts").read_text(encoding="utf-8")
    assert '"available_tool_success"' in source
    assert '"fictional_tool_reject"' in source
    assert {path.stem for path in FIXTURES.glob("*.json")} == {
        "available_tool_success",
        "fictional_tool_reject",
    }


def test_success_fixture_is_standard_openai_and_mcp_wire() -> None:
    case = fixture("available_tool_success")
    assert case["ui"]["chat_id"] == "chat_fixture_available"
    assert case["ui"]["message_id"] == "msg_fixture_available"
    call_id = case["ui"]["tool_call_id"]

    request = case["openai"]["request"]
    completion = case["openai"]["completion"]
    assert request["tools"]
    assert all(tool["type"] == "function" for tool in request["tools"])
    assistant = completion["choices"][0]["message"]
    call = assistant["tool_calls"][0]
    assert call["id"] == call_id
    assert call["type"] == "function"
    assert isinstance(call["function"]["arguments"], str)

    methods = [hop["http"]["request"]["method"] for hop in case["mcp"]]
    assert methods == EXPECTED_METHODS
    for hop in case["mcp"]:
        assert hop["http"]["method"] == "POST"
        assert hop["http"]["path"] == "/mcp"
        assert hop["http"]["request"]["jsonrpc"] == "2.0"
        assert "event" not in hop["http"]["request"]
        if hop["http"]["request"]["method"] == "notifications/initialized":
            assert hop["http"]["status"] == 202
            continue
        assert hop["http"]["status"] == 200
        assert hop["http"]["response"]["jsonrpc"] == "2.0"
        assert hop["http"]["response"]["id"] == hop["http"]["request"]["id"]
    tool_message = case["openai"]["followup"]["request"]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == call_id


def test_rejection_fixture_never_invents_an_openai_tool_call() -> None:
    case = fixture("fictional_tool_reject")
    message = case["openai"]["completion"]["choices"][0]["message"]
    assert message["role"] == "assistant"
    assert message.get("tool_calls") in (None, [])
    assert case["ui"]["tool_call_id"] is None
    assert case["expected"]["mcp_called"] == []


def test_fixture_contains_no_custom_event_protocol() -> None:
    for path in FIXTURES.glob("*.json"):
        raw = path.read_text(encoding="utf-8")
        case = json.loads(raw)
        assert '"event":' not in raw
        assert all(hop["http"]["request"]["method"] in EXPECTED_METHODS for hop in case["mcp"])
        assert case["mcp"][0]["http"]["request"]["method"] == "initialize"
