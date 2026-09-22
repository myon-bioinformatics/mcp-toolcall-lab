"""Opt-in backend benchmark for measured Jev/OpenAI-compatible routing decisions."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from mcp_toolcall_lab.jev_backend import Backend
from mcp_toolcall_lab.jev_router import calibration, confusion_matrix, decide_route



def urllib_json_post(url: str, body: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OpenAI-compatible endpoint returned non-object JSON")
    return payload


def run_backend_routing_benchmark(
    cases: list[dict[str, Any]],
    backend: Backend,
    *,
    threshold: float = 0.75,
) -> dict[str, Any]:
    """Measure a real backend's routing decisions without executing MCP tools.

    Unlike the deterministic fixture benchmark, this calls the supplied backend
    for every case. The report therefore describes measured backend decisions,
    while tool execution/success remains a separate integration concern.
    """
    rows: list[dict[str, Any]] = []
    for case in cases:
        decision = decide_route(
            backend,
            case["state"],
            threshold=threshold,
            meta={"benchmark_case": case["id"], "benchmark_mode": "backend-routing"},
        )
        rows.append({
            "id": case["id"],
            "expected_tool_family": str(case["expected_tool_family"]),
            "needs_tool": decision.needs_tool,
            "tool_family": decision.tool_family,
            "confidence": decision.confidence,
            "prob_source": decision.prob_source,
            "decision_ms": decision.decision_ms,
            "fallback_reason": decision.fallback_reason,
        })

    matrix = confusion_matrix(rows)
    routed = matrix["routed-correct"] + matrix["routed-wrong"]
    return {
        "mode": "measured-backend-routing",
        "prob_sources": sorted({str(row["prob_source"]) for row in rows}),
        "cases": len(rows),
        "routed": routed,
        "fallbacks": matrix["fallback-correct"] + matrix["fallback-wrong"],
        "wrong_routes": matrix["routed-wrong"],
        # Confidence/policy estimate only: routed-wrong is intentionally included
        # because the selector call was skipped even though correctness regressed.
        "selector_calls_skipped_by_policy": routed,
        "decision_ms": round(sum(float(row["decision_ms"]) for row in rows), 3),
        "confusion_matrix": matrix,
        "calibration": calibration(rows),
        "rows": rows,
    }
