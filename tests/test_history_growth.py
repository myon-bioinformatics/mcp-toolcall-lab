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


def _full_metric_row():
    return {"kind": "chat.completions", "wire_messages": [],
            "content_measurement": "observed_request", "content_chars": 0,
            "tool_content_chars": 0,
            "role_content_chars": dict.fromkeys(["system", "developer", "user", "assistant", "tool"], 0),
            "message_measurement": "observed_request", "message_json_chars": 2,
            "tool_call_argument_chars": 0, "message_json_format": "unicode_sorted_compact_v1"}


@pytest.mark.parametrize("field,value", [
    ("message_json_chars", True), ("message_json_chars", -1), ("message_json_chars", 1),
    ("message_json_chars", "2"), ("tool_call_argument_chars", False),
    ("tool_call_argument_chars", -1), ("tool_call_argument_chars", 3),
    ("tool_call_argument_chars", None), ("message_json_format", "unknown"),
])
def test_invalid_full_metrics_are_never_reported_as_total(field, value):
    row = _full_metric_row()
    row[field] = value
    result = measure([row])[0]
    assert result["message_measurement"] == "invalid_metrics"
    assert result["message_json_chars"] is None
    assert result["tool_call_argument_chars"] is None


def test_legacy_content_only_log_does_not_claim_full_history_measurement():
    row = _full_metric_row()
    del row["message_measurement"]
    del row["message_json_chars"]
    del row["tool_call_argument_chars"]
    result = measure([row])[0]
    assert result["content_measurement"] == "observed_request"
    assert result["content_chars"] == 0
    assert result["message_measurement"] == "unavailable_in_legacy_log"
    assert result["message_json_chars"] is None
    assert result["tool_call_argument_chars"] is None
    row["content_measurement"] = "invalid_messages"
    assert measure([row])[0]["message_measurement"] == "invalid_messages"
