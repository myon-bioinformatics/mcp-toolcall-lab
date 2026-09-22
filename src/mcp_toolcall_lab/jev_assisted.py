"""Optional Jev pre-route executor for the Ironmate MCP boundary."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_backend import Backend
from mcp_toolcall_lab.jev_router import RouteDecision, decide_route

Fallback = Callable[[Any], Any]
DirectCall = Callable[[], Any]


@dataclass(frozen=True)
class AssistedResult:
    decision: RouteDecision
    llm_used: bool
    tool_called: bool
    result: Any
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    execution_fallback_reason: str | None = None


def _execute_direct(
    call: DirectCall,
    *,
    decision: RouteDecision,
    state: Any,
    fallback: Fallback,
    error_reason: str,
) -> AssistedResult:
    """Run a direct tool boundary with shared timing and fallback semantics."""
    started = time.monotonic()
    try:
        result = call()
    except Exception:
        tool_ms = round((time.monotonic() - started) * 1000, 3)
        fallback_started = time.monotonic()
        result = fallback(state)
        return AssistedResult(
            decision=decision,
            llm_used=True,
            tool_called=True,
            result=result,
            llm_ms=round((time.monotonic() - fallback_started) * 1000, 3),
            tool_ms=tool_ms,
            execution_fallback_reason=error_reason,
        )
    return AssistedResult(
        decision=decision,
        llm_used=False,
        tool_called=True,
        result=result,
        tool_ms=round((time.monotonic() - started) * 1000, 3),
    )


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
    direct_calls: dict[str, DirectCall] | None = None,
) -> AssistedResult:
    """Pre-route only safe cases; preserve the existing fallback for everything else.

    Ironmate keeps its traced client boundary. Other high-confidence families can
    opt into direct execution through caller-injected zero-argument callables.
    Unsupported families and all policy fallbacks preserve the existing LLM flow.
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

    if (
        decision.fallback_reason is None
        and decision.tool_family in {"wikipedia", "mock"}
        and direct_calls
        and decision.tool_family in direct_calls
    ):
        return _execute_direct(
            direct_calls[decision.tool_family],
            decision=decision,
            state=state,
            fallback=fallback,
            error_reason=f"{decision.tool_family}_call_error",
        )

    if decision.tool_family == "ironmate" and decision.fallback_reason is None:
        return _execute_direct(
            lambda: ironmate_client.call(
                ironmate_tool,
                ironmate_arguments,
                meta={"source": "jev-router", **(meta or {})},
                debug=debug,
            ),
            decision=decision,
            state=state,
            fallback=fallback,
            error_reason="ironmate_call_error",
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
        "execution_fallback_reason": result.execution_fallback_reason,
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
        "execution_fallbacks": sum(bool(row.get("execution_fallback_reason")) for row in rows),
        "out_of_scope_family": sum(
            bool(row.get("llm_used"))
            and not bool(row.get("fallback_reason"))
            and not bool(row.get("execution_fallback_reason"))
            for row in rows
        ),
        "decision_ms": round(sum(float(row.get("decision_ms", 0.0)) for row in rows), 3),
        "llm_ms": round(sum(float(row.get("llm_ms", 0.0)) for row in rows), 3),
        "tool_ms": round(sum(float(row.get("tool_ms", 0.0)) for row in rows), 3),
        "tool_successes": sum(successes),
        "tool_attempts": len(successes),
    }
