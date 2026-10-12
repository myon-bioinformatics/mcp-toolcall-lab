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


def test_observed_counts_and_legacy_are_distinct():
    roles = {"assistant": 0, "developer": 0, "system": 0, "tool": 7, "user": 4}
    rows = [
        {"kind": "chat.completions", "chat_id": "A", "completion_id": "new",
         "wire_messages": [{"role": "user"}, {"role": "tool"}],
         "content_measurement": "observed_request", "content_chars": 11,
         "role_content_chars": roles, "tool_content_chars": 7},
        {"kind": "chat.completions", "chat_id": "A", "completion_id": "old",
         "wire_messages": [{"role": "user"}]},
    ]
    new, old = measure(rows)
    assert (new["completion_id"], new["turn_index"], new["content_chars"],
            new["role_content_chars"]["tool"], new["tool_content_chars"]) == ("new", 1, 11, 7, 7)
    assert old["turn_index"] == 2
    assert old["content_chars"] is None
    assert old["role_content_chars"] is None
    assert old["content_measurement"] == "unavailable_in_wire_projection"


def test_invalid_or_forged_metric_is_not_treated_as_measured():
    row = {"kind": "chat.completions", "wire_messages": [],
           "content_measurement": "observed_request", "content_chars": 5,
           "tool_content_chars": 0, "role_content_chars": {"user": 5}}
    assert measure([row])[0]["content_measurement"] == "invalid_metrics"
    assert measure([row])[0]["content_chars"] is None
