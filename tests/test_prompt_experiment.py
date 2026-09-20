"""Offline prompt/model experiment fixtures — no sockets, no API keys."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS, TOOL_DESCRIPTIONS
from mcp_toolcall_lab.mcp_http import extract_sse_data
from mcp_toolcall_lab.prompt_experiment import (
    AUDIT_KEYS,
    DEFAULT_FIXTURE_DIR,
    MCP_INITIALIZE,
    MCP_INITIALIZED,
    MCP_TOOLS_CALL,
    MCP_TOOLS_LIST,
    VERDICT_FAIL,
    VERDICT_PASS,
    hop_method,
    hop_request,
    hop_response,
    hop_was_sent,
    iter_cases,
    iter_mcp_hops,
    load_case,
    main,
    mcp_call_hops,
    replay_case,
    replay_fixtures,
    request_header,
    response_header,
    tool_result_messages,
    write_audit,
)
from tests.test_schema import EXPECTED_TOOL_SPECS

ROOT = Path(__file__).resolve().parents[1]
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
        assert specs == EXPECTED_TOOL_SPECS


def test_available_tool_success_tools_call_send_and_return() -> None:
    case = _cases()["available_tool_success"]
    hop = _hops_by_method(case)[MCP_TOOLS_CALL]
    request = hop_request(hop)
    result = hop_response(hop)["result"]
    params = request["params"]
    ui = case["ui"]
    assert request["method"] == MCP_TOOLS_CALL
    assert params["name"] == "find_municipalities"
    assert params["arguments"] == {"query": "Yokohama"}
    assert params["_meta"]["chat_id"] == ui["chat_id"]
    assert params["_meta"]["call_id"] == ui["tool_call_id"]
    assert request_header(hop, "X-Chat-Id") == ui["chat_id"]
    assert request_header(hop, "X-OpenWebUI-Message-Id") == ui["message_id"]
    assert request_header(hop, "Mcp-Session-Id") == ui["mcp_session_id"]
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == [
        {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}
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
    assert "query_reinfoldib" in user
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
    """If query_reinfoldib is sent, MCP returns isError — recorded, not conversation path."""
    case = _cases()["fictional_tool_reject"]
    hop = case["mcp_rejected_call"]
    request = hop_request(hop)
    result = hop_response(hop)["result"]
    ui = case["ui"]
    assert hop_was_sent(hop) is True
    assert hop not in iter_mcp_hops(case)
    assert request["jsonrpc"] == "2.0"
    assert request["method"] == MCP_TOOLS_CALL
    assert request["params"]["name"] == "query_reinfoldib"
    assert request_header(hop, "X-Chat-Id") == ui["chat_id"]
    assert request_header(hop, "X-OpenWebUI-Message-Id") == ui["message_id"]
    assert request_header(hop, "Mcp-Session-Id") == ui["mcp_session_id"]
    assert hop["http"]["status"] == 200
    parsed = extract_sse_data(hop["http"]["response_sse"])
    assert parsed == hop_response(hop)
    assert result["isError"] is True
    assert "query_reinfoldib" in result["content"][0]["text"]


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
                    "function": {"name": "query_reinfoldib", "arguments": "{}"},
                }
            ],
        },
    }
    case["mcp"].append(case["mcp_rejected_call"])
    case["expected"] = {
        "verdict": VERDICT_FAIL,
        "selected_tools": ["query_reinfoldib"],
        "fictional_tools": ["query_reinfoldib"],
    }
    audit = replay_case(case)
    assert audit["verdict"] == VERDICT_FAIL
    assert audit["fictional_tools"] == ["query_reinfoldib"]
    assert audit["mcp_called"] == ["query_reinfoldib"]
    assert audit["server_accepted"] is False
    assert audit["outcome"] == "error"
    assert audit["matched_expected"] is True


def test_audit_jsonl_is_comparison_fields_only(tmp_path: Path) -> None:
    audits = replay_fixtures()
    path = tmp_path / "prompt-experiments.jsonl"
    write_audit(path, audits)
    text = path.read_text(encoding="utf-8")
    assert "Yokohama" not in text
    assert "query_reinfoldib" not in text
    assert "arguments" not in text
    assert "api_key" not in text
    assert "REINFOLIB" not in text
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
    assert main(["replay", "--fixtures", str(DEFAULT_FIXTURE_DIR), "--out", str(out)]) == 0
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
