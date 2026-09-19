"""Offline prompt/model experiment fixtures — no sockets, no API keys."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS, TOOL_DESCRIPTIONS
from mcp_toolcall_lab.prompt_experiment import (
    AUDIT_KEYS,
    DEFAULT_FIXTURE_DIR,
    VERDICT_FAIL,
    VERDICT_PASS,
    iter_cases,
    load_case,
    main,
    replay_case,
    replay_fixtures,
    write_audit,
)
from mcp_toolcall_lab.record import EVENT_INITIALIZE, EVENT_TOOLS_CALL, EVENT_TOOLS_LIST

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "system_prompts" / "strict_tool_selection.md"


def _cases() -> dict[str, dict]:
    return {case["id"]: case for case in iter_cases()}


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
        events = case["mcp"]
        assert events[0]["event"] == EVENT_INITIALIZE
        assert any(event.get("event") == EVENT_TOOLS_LIST for event in events)


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
    assert any(event.get("event") == EVENT_TOOLS_CALL for event in _cases()["available_tool_success"]["mcp"])


def test_fictional_tool_reject_replays_pass_without_tool_calls() -> None:
    case = _cases()["fictional_tool_reject"]
    user = case["openai"]["request"]["messages"][1]["content"]
    assert "query_reinfoldib" in user
    audit = replay_case(case)
    assert audit["verdict"] == VERDICT_PASS
    assert audit["matched_expected"] is True
    assert audit["selected_tools"] == []
    assert audit["fictional_tools"] == []
    assert audit["mcp_called"] == []
    assert audit["finish_reason"] == "stop"
    assert audit["raw_schema_valid"] is None
    assert not any(event.get("event") == EVENT_TOOLS_CALL for event in case["mcp"])


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
    case["mcp"].append({"event": "tools/call", "tool": "query_reinfoldib", "outcome": "error"})
    case["expected"] = {
        "verdict": VERDICT_FAIL,
        "selected_tools": ["query_reinfoldib"],
        "fictional_tools": ["query_reinfoldib"],
    }
    audit = replay_case(case)
    assert audit["verdict"] == VERDICT_FAIL
    assert audit["fictional_tools"] == ["query_reinfoldib"]
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
    loaded = load_case(path)
    assert loaded["openai"]["request"]["messages"][0]["content"] == PROMPT.read_text(
        encoding="utf-8"
    ).strip()
