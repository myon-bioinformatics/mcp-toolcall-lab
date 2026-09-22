from __future__ import annotations

import json

from mcp_toolcall_lab.jev_backend import FixtureBackend
from mcp_toolcall_lab.jev_router import calibration, confusion_matrix, decide_route, latency_summary


def test_high_confidence_tool_route() -> None:
    backend = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.95},
        "tool_family": {"type": "choice", "choice": "wikipedia", "confidence": 0.9,
                        "probabilities": {"ironmate": 0.02, "wikipedia": 0.9, "mock": 0.03, "none": 0.05}},
    })
    decision = decide_route(backend, "Find a Wikipedia section")
    assert decision.needs_tool is True
    assert decision.tool_family == "wikipedia"
    assert decision.fallback_reason is None
    assert decision.confidence == 0.9


def test_low_confidence_becomes_fallback() -> None:
    backend = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.6},
        "tool_family": {"type": "choice", "choice": "ironmate", "confidence": 0.7,
                        "probabilities": {"ironmate": 0.7, "wikipedia": 0.1, "mock": 0.1, "none": 0.1}},
    })
    decision = decide_route(backend, "ambiguous")
    assert decision.fallback_reason == "low_confidence"


def test_no_tool_skips_family_question() -> None:
    backend = FixtureBackend(fixtures={"needs_tool": {"type": "noul", "noul": 0.1}})
    decision = decide_route(backend, "hello")
    assert decision.needs_tool is False
    assert decision.tool_family == "none"
    assert decision.fallback_reason is None


def test_route_decision_is_traced(tmp_path, monkeypatch) -> None:
    log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    backend = FixtureBackend(fixtures={"needs_tool": {"type": "noul", "noul": 0.1}})
    decide_route(backend, "hello", debug={"chat_id": "chat_1"})
    row = json.loads(log.read_text(encoding="utf-8"))
    assert row["event"] == "jev/route_decision"
    assert row["prob_source"] == "fixture"
    assert row["chat_id"] == "chat_1"
    assert row["decision_ms"] >= 0


def test_confusion_matrix_and_latency_are_explicit() -> None:
    rows = [
        {"tool_family": "wikipedia", "expected_tool_family": "wikipedia", "fallback_reason": None,
         "decision_ms": 1, "llm_ms": 0, "tool_ms": 5, "prob_source": "fixture", "confidence": .9},
        {"tool_family": "ironmate", "expected_tool_family": "mock", "fallback_reason": "low_confidence",
         "decision_ms": 2, "llm_ms": 10, "tool_ms": 0, "prob_source": "fixture", "confidence": .6},
    ]
    assert confusion_matrix(rows) == {
        "routed-correct": 1, "routed-wrong": 0, "fallback-correct": 0, "fallback-wrong": 1
    }
    assert latency_summary(rows) == {"decision_ms": 3.0, "llm_ms": 10.0, "tool_ms": 5.0}


def test_calibration_never_pools_probability_sources() -> None:
    rows = [
        {"tool_family": "none", "expected_tool_family": "none", "prob_source": "fixture", "confidence": .8},
        {"tool_family": "none", "expected_tool_family": "ironmate", "prob_source": "self_reported", "confidence": .8},
    ]
    result = calibration(rows)
    assert set(result) == {"fixture", "self_reported"}
    assert result["fixture"]["n"] == 1
    assert result["self_reported"]["n"] == 1
