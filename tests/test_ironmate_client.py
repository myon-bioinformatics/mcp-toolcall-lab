from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_toolcall_lab.ironmate_client import IronmateClient, normalize_catalog

FIXTURE = Path(__file__).parents[1] / "fixtures" / "ironmate" / "catalog.json"


class FakeSession:
    def __init__(self, tools):
        self.tools = tools
        self.methods = []

    def initialize(self):
        self.methods.append("initialize")
        return {"result": {}}

    def list_tools(self):
        self.methods.append("tools/list")
        return {"result": {"tools": self.tools}}

    def call_tool(self, name, arguments, **kwargs):
        self.methods.append("tools/call")
        return {"result": {"content": [{"type": "text", "text": f"{name}:{arguments['query']}"}]}}


def _snapshot_tools():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["tools"]


def test_discovery_matches_offline_snapshot():
    expected = _snapshot_tools()
    session = FakeSession(list(reversed(expected)))
    assert IronmateClient(session).discover() == expected
    assert session.methods == ["initialize", "tools/list"]


def test_representative_call_is_delegated_without_business_logic():
    session = FakeSession(_snapshot_tools())
    result = IronmateClient(session).call("search_repository_metadata", {"query": "markdown"})
    assert session.methods == ["tools/call"]
    assert result["result"]["content"][0]["text"] == "search_repository_metadata:markdown"


def test_catalog_rejects_missing_tools():
    with pytest.raises(ValueError, match="result.tools"):
        normalize_catalog({"result": {}})
