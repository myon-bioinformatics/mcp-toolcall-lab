"""Typed Jev-assisted tool-family routing, kept separate from backend transport."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from mcp_toolcall_lab.jev_backend import Backend
from mcp_toolcall_lab.jev_shim import brier_score
from mcp_toolcall_lab.jev_typesafe import choice_question, noul_question
from mcp_toolcall_lab.mock.common import append_jsonl

EVENT_ROUTE_DECISION = "jev/route_decision"
TOOL_FAMILIES = ("ironmate", "wikipedia", "mock", "none")


@dataclass(frozen=True)
class RouteDecision:
    needs_tool: bool
    tool_family: str
    confidence: float
    prob_source: str
    decision_ms: float
    fallback_reason: str | None = None


def _confidence(answer: dict[str, Any]) -> float:
    if answer["type"] == "noul":
        return max(float(answer["noul"]), 1.0 - float(answer["noul"]))
    return float(answer["confidence"])


def decide_route(
    backend: Backend,
    state: Any,
    *,
    threshold: float = 0.75,
    meta: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> RouteDecision:
    """Return a typed pre-route; low confidence falls back to the existing LLM."""
    started = time.monotonic()
    needs = backend.answer(
        "needs_tool",
        noul_question(instructions="Does this request require an external tool?"),
        state,
    )
    needs_tool = float(needs["noul"]) >= 0.5
    confidence = _confidence(needs)
    family = "none"
    fallback_reason = None

    if needs_tool:
        family_answer = backend.answer(
            "tool_family",
            choice_question(
                {name: None for name in TOOL_FAMILIES},
                instructions="Choose the single best tool family for this request.",
            ),
            state,
        )
        family = str(family_answer["choice"])
        confidence = min(confidence, _confidence(family_answer))
        if family == "none":
            fallback_reason = "needs_tool_but_no_family"

    if confidence < threshold:
        fallback_reason = "low_confidence"
    if family not in TOOL_FAMILIES:
        family = "none"
        fallback_reason = "unknown_family"

    decision = RouteDecision(
        needs_tool=needs_tool,
        tool_family=family,
        confidence=confidence,
        prob_source=backend.prob_source,
        decision_ms=(time.monotonic() - started) * 1000,
        fallback_reason=fallback_reason,
    )
    record_route_decision(decision, meta=meta, debug=debug)
    return decision


def record_route_decision(
    decision: RouteDecision,
    *,
    meta: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> None:
    path = os.environ.get("MCP_TOOLCALL_LOG")
    if not path:
        return
    row: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "event": EVENT_ROUTE_DECISION, **asdict(decision)}
    if meta:
        row["meta"] = meta
    if debug:
        row["debug"] = debug
        if debug.get("chat_id"):
            row["chat_id"] = debug["chat_id"]
    append_jsonl(path, row)


def confusion_matrix(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Four-way matrix requested by #39 for routed vs fallback correctness."""
    out = {"routed-correct": 0, "routed-wrong": 0, "fallback-correct": 0, "fallback-wrong": 0}
    for row in rows:
        fallback = bool(row.get("fallback_reason"))
        correct = row.get("tool_family") == row.get("expected_tool_family")
        out[("fallback-" if fallback else "routed-") + ("correct" if correct else "wrong")] += 1
    return out


def calibration(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    """Brier summaries partitioned by prob_source; incomparable sources never mix."""
    grouped: dict[str, list[tuple[float, bool]]] = {}
    for row in rows:
        source = str(row["prob_source"])
        grouped.setdefault(source, []).append(
            (float(row["confidence"]), row.get("tool_family") == row.get("expected_tool_family"))
        )
    return {source: {"n": len(values), "brier_score": brier_score(values)} for source, values in grouped.items()}


def latency_summary(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Keep decision, fallback-LLM, and tool latency as separate measurements."""
    keys = ("decision_ms", "llm_ms", "tool_ms")
    return {key: sum(float(row.get(key, 0.0)) for row in rows) for key in keys}
