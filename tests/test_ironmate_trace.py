from __future__ import annotations

import json

import pytest

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.record import read_jsonl


class FakeSession:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = []

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return {"result": {"tools": []}}

    def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if self.error:
            raise self.error
        return {"result": {"content": [{"type": "text", "text": "ok"}]}}


def test_representative_ironmate_call_records_shared_trace(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    session = FakeSession()
    client = IronmateClient(session)

    result = client.call(
        "search_repository_metadata",
        {"query": "markdown"},
        meta={"source": "jev-router", "trace_id": "trace_ironmate_1"},
        debug={"chat_id": "chat_ironmate_1"},
    )

    assert result["result"]["content"][0]["text"] == "ok"
    assert session.calls[0][2]["meta"]["source"] == "jev-router"
    [row] = read_jsonl(str(log))
    assert row["event"] == "tools/call"
    assert row["tool"] == "search_repository_metadata"
    assert row["arguments"] == {"query": "markdown"}
    assert row["outcome"] == "success"
    assert row["meta"]["trace_id"] == "trace_ironmate_1"
    assert row["chat_id"] == "chat_ironmate_1"
    assert row["duration_ms"] >= 0


def test_failed_ironmate_call_records_error_then_reraises(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    client = IronmateClient(FakeSession(error=RuntimeError("ironmate unavailable")))

    with pytest.raises(RuntimeError, match="ironmate unavailable"):
        client.call("search_repository_metadata", {"query": "markdown"})

    [row] = read_jsonl(str(log))
    assert row["event"] == "tools/call"
    assert row["outcome"] == "error"
    assert row["error"] == "ironmate unavailable"
    assert row["duration_ms"] >= 0
