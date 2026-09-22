"""Optional Jev pre-route executor for the Ironmate MCP boundary."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_backend import Backend
from mcp_toolcall_lab.jev_router import RouteDecision, decide_route

Fallback = Callable[[Any], Any]


@dataclass(frozen=True)
class AssistedResult:
    decision: RouteDecision
    llm_used: bool
    tool_called: bool
    result: Any
    llm_ms: float = 0.0
    tool_ms: float = 0.0


def execute_jev_assisted(
    backend: Backend,
    state: Any,
    *,
    ironmate_client: IronmateClient,
    ironmate_tool: str,
    ironmate_arguments: dict[str, Any],
    fallback: Fallback,
    threshold: float = 0.75,
    meta: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> AssistedResult:
    """Pre-route only safe cases; preserve the existing fallback for everything else.

    This first integration slice directly executes only a high-confidence Ironmate
    route. Wikipedia/mock and all fallback decisions deliberately continue through
    the caller-supplied existing LLM flow.
    """
    decision = decide_route(
        backend, state, threshold=threshold, meta=meta, debug=debug
    )

    if not decision.needs_tool and decision.fallback_reason is None:
        return AssistedResult(
            decision=decision,
            llm_used=False,
            tool_called=False,
            result=None,
        )

    if decision.tool_family == "ironmate" and decision.fallback_reason is None:
        started = time.monotonic()
        result = ironmate_client.call(
            ironmate_tool,
            ironmate_arguments,
            meta={"source": "jev-router", **(meta or {})},
            debug=debug,
        )
        return AssistedResult(
            decision=decision,
            llm_used=False,
            tool_called=True,
            result=result,
            tool_ms=round((time.monotonic() - started) * 1000, 3),
        )

    started = time.monotonic()
    result = fallback(state)
    return AssistedResult(
        decision=decision,
        llm_used=True,
        tool_called=False,
        result=result,
        llm_ms=round((time.monotonic() - started) * 1000, 3),
    )


def benchmark_row(
    result: AssistedResult,
    *,
    expected_tool_family: str,
    tool_success: bool | None = None,
) -> dict[str, Any]:
    """Normalize one assisted execution into the offline #39 comparison surface."""
    row = {
        **asdict(result.decision),
        "expected_tool_family": expected_tool_family,
        "llm_used": result.llm_used,
        "llm_avoided": not result.llm_used,
        "tool_called": result.tool_called,
        "llm_ms": result.llm_ms,
        "tool_ms": result.tool_ms,
    }
    if tool_success is not None:
        row["tool_success"] = tool_success
    return row


def benchmark_summary(rows: list[dict[str, Any]]) -> dict[str, int | float]:
    """Aggregate the measurements requested by #39 without mixing correctness."""
    total = len(rows)
    wrong_routes = sum(
        1
        for row in rows
        if not row.get("fallback_reason")
        and row.get("tool_family") != row.get("expected_tool_family")
    )
    successes = [bool(row["tool_success"]) for row in rows if "tool_success" in row]
    return {
        "cases": total,
        "llm_calls_avoided": sum(bool(row.get("llm_avoided")) for row in rows),
        "wrong_routes": wrong_routes,
        "policy_fallbacks": sum(bool(row.get("fallback_reason")) for row in rows),
        "out_of_scope_family": sum(
            bool(row.get("llm_used")) and not bool(row.get("fallback_reason"))
            for row in rows
        ),
        "decision_ms": round(sum(float(row.get("decision_ms", 0.0)) for row in rows), 3),
        "llm_ms": round(sum(float(row.get("llm_ms", 0.0)) for row in rows), 3),
        "tool_ms": round(sum(float(row.get("tool_ms", 0.0)) for row in rows), 3),
        "tool_successes": sum(successes),
        "tool_attempts": len(successes),
    }
