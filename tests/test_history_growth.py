"""Tests for read-only history growth telemetry."""
import json

import pytest

from mcp_toolcall_lab.history_growth import load_jsonl, measure


def test_growth_by_chat_and_tool_count():
    rows = [
        {"kind": "chat.completions", "chat_id": "A", "completion_id": "1",
         "wire_messages": [{"role": "user"}]},
        {"kind": "chat.completions", "chat_id": "B", "completion_id": "2",
         "wire_messages": [{"role": "user"}, {"role": "assistant"}]},
        {"kind": "chat.completions", "chat_id": "A", "completion_id": "3",
         "wire_messages": [{"role": "user"}, {"role": "assistant",
         "tool_calls": [{"id": "c1"}]}, {"role": "tool", "tool_call_id": "c1"},
         {"role": "user"}]},
    ]
    result = measure(rows)
    assert [(x["chat_id"], x["turn_index"]) for x in result] == [
        ("A", 1), ("B", 1), ("A", 2),
    ]
    assert result[-1]["message_count"] == 4
    assert result[-1]["tool_message_count"] == 1
    assert result[-1]["content_chars"] is None
    assert result[-1]["projection_json_chars"] == len(
        json.dumps(rows[-1]["wire_messages"], ensure_ascii=False)
    )


def test_unknown_chat_ids_are_not_merged():
    rows = [{"kind": "chat.completions", "wire_messages": []} for _ in range(2)]
    assert [x["turn_index"] for x in measure(rows)] == [1, 1]


def test_skip_incomplete_rows_and_non_completions():
    assert measure([
        {"kind": "other", "wire_messages": []},
        {"kind": "chat.completions", "wire_messages": "not a list"},
        {"kind": "chat.completions", "wire_messages": [None]},
    ]) == []


def test_jsonl_parser_rejects_non_object(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"kind":"chat.completions","wire_messages":[]}\n[]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_jsonl(path)


def test_jsonl_parser_accepts_blank_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('\n{"kind":"chat.completions","wire_messages":[]}\n', encoding="utf-8")
    assert len(measure(load_jsonl(path))) == 1
