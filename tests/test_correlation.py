from __future__ import annotations

import os

from mcp_toolcall_lab.correlation import correlation_key, normalize_correlation


def test_preserves_product_ids_and_generates_lab_ids() -> None:
    meta = normalize_correlation({"chat_id": "chat-real", "call_id": "call-1"}, source="openwebui")
    assert meta["chat_id"] == "chat-real"
    assert meta["call_id"] == "call-1"
    assert meta["trace_id"].startswith("trace_")
    assert meta["request_id"].startswith("req_")
    assert meta["source"] == "openwebui"


def test_environment_is_a_frontend_bridge(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CHAT_ID", "chat-env")
    monkeypatch.setenv("MCP_TRACE_ID", "trace-env")
    meta = normalize_correlation({})
    assert meta["chat_id"] == "chat-env"
    assert meta["trace_id"] == "trace-env"


def test_never_fabricates_product_chat_id(monkeypatch) -> None:
    monkeypatch.delenv("MCP_CHAT_ID", raising=False)
    monkeypatch.delenv("MCP_CONVERSATION_ID", raising=False)
    meta = normalize_correlation({})
    assert "chat_id" not in meta
    assert "conversation_id" not in meta


def test_correlation_key_prefers_product_then_lab_ids() -> None:
    assert correlation_key({"chat_id": "c", "trace_id": "t"}) == "c"
    assert correlation_key({"trace_id": "t"}) == "t"
