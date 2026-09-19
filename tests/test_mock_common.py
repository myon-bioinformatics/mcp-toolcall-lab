"""Shared mock helpers — stdlib only, no FastMCP, no OpenAI decision logic."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_toolcall_lab.chat_sim import new_call_id as sim_call_id
from mcp_toolcall_lab.chat_sim import new_chat_id as sim_chat_id
from mcp_toolcall_lab.mock.common import (
    append_jsonl,
    chat_id_from_headers,
    close_http11_sse,
    id_from_headers,
    new_call_id,
    new_chat_id,
    read_jsonl,
)
from mcp_toolcall_lab.record import new_call_id as record_call_id
from mcp_toolcall_lab.record import new_chat_id as record_chat_id
from mcp_toolcall_lab.record import read_jsonl as record_read_jsonl


def test_mints_and_jsonl_reader_live_in_mock_common() -> None:
    assert record_chat_id is new_chat_id
    assert record_call_id is new_call_id
    assert sim_chat_id is new_chat_id
    assert sim_call_id is new_call_id
    assert record_read_jsonl is read_jsonl
    assert new_chat_id().startswith("chat_")
    assert new_call_id().startswith("call_")


def test_header_lookup_is_case_insensitive() -> None:
    assert chat_id_from_headers({"X-Chat-Id": "chat_from_header"}) == "chat_from_header"
    assert chat_id_from_headers({"X-OpenWebUI-Chat-Id": "owui-real-chat"}) == "owui-real-chat"
    assert id_from_headers({"X-Message-Id": "m1"}, ("x-message-id",)) == "m1"
    assert chat_id_from_headers({"accept": "application/json"}) is None


def test_append_jsonl_creates_parent_and_read_skips_junk(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    append_jsonl(path, {"n": 1})
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n\n", encoding="utf-8")
    append_jsonl(path, {"n": 2})
    events = read_jsonl(path)
    assert [row["n"] for row in events] == [1, 2]


def test_close_http11_sse_sets_connection_close() -> None:
    class _Handler:
        def __init__(self) -> None:
            self.headers: list[tuple[str, str]] = []
            self.close_connection = False

        def send_header(self, key: str, value: str) -> None:
            self.headers.append((key, value))

    handler = _Handler()
    close_http11_sse(handler)
    assert ("Connection", "close") in handler.headers
    assert handler.close_connection is True
