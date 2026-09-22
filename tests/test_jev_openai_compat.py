"""Offline tests for the P1 OpenAI-compatible Jev backend."""
from __future__ import annotations

import json

import pytest

from mcp_toolcall_lab.jev_backend import BackendError, answer_with_trace
from mcp_toolcall_lab.jev_openai_compat import (
    PROB_SOURCE_SELF_REPORTED,
    OpenAICompatBackend,
    answer_schema,
)
from mcp_toolcall_lab.jev_typesafe import noul_question


def _post_with(payload: dict, captured: dict):
    def post(url: str, body: dict) -> dict:
        captured["url"] = url
        captured["body"] = body
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}
    return post


def test_noul_uses_llama_cpp_chat_completions_and_real_wire() -> None:
    captured = {}
    backend = OpenAICompatBackend(
        base_url="http://cpu-llm:8080/",
        model="tiny",
        post=_post_with({"type": "noul", "noul": 0.8}, captured),
    )
    assert backend.answer("needs_tool", noul_question(), "hello") == {"type": "noul", "noul": 0.8}
    assert backend.prob_source == PROB_SOURCE_SELF_REPORTED
    assert captured["url"] == "http://cpu-llm:8080/v1/chat/completions"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["schema"]["required"] == ["type", "noul"]


def test_choice_schema_uses_probabilities_not_legacy_distribution() -> None:
    schema = answer_schema({"type": "choice", "criteria": {"ironmate": None, "none": None}})
    assert "probabilities" in schema["properties"]
    assert "distribution" not in schema["properties"]
    assert schema["properties"]["choice"]["enum"] == ["ironmate", "none"]


def test_score_schema_has_ordinal_legend_and_probabilities() -> None:
    schema = answer_schema({"type": "score", "criteria": ["low", "high"]})
    assert schema["properties"]["legend"]["required"] == ["0", "1"]
    assert schema["properties"]["probabilities"]["required"] == ["0", "1"]


def test_invalid_model_payload_is_rejected_by_shared_wire_validator() -> None:
    backend = OpenAICompatBackend(
        base_url="http://unused",
        model="tiny",
        post=lambda url, body: {"choices": [{"message": {"content": '{"probability":0.9}'}}]},
    )
    with pytest.raises(BackendError, match="not a valid"):
        backend.answer("q", noul_question(), "state")


def test_non_json_completion_is_rejected() -> None:
    backend = OpenAICompatBackend(
        base_url="http://unused",
        model="tiny",
        post=lambda url, body: {"choices": [{"message": {"content": "yes"}}]},
    )
    with pytest.raises(BackendError, match="not valid JSON"):
        backend.answer("q", noul_question(), "state")


def test_trace_records_self_reported_probability_source(tmp_path, monkeypatch) -> None:
    log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    backend = OpenAICompatBackend(
        base_url="http://cpu-llm:8080",
        model="tiny",
        post=lambda url, body: {"choices": [{"message": {"content": '{"type":"noul","noul":0.6}'}}]},
    )
    answer_with_trace(backend, "needs_tool", noul_question(), "state")
    row = json.loads(log.read_text(encoding="utf-8"))
    assert row["backend"] == "OpenAICompatBackend"
    assert row["prob_source"] == "self_reported"
