"""Deterministic #39 benchmark harness for Jev-assisted routing policy."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_assisted import benchmark_row, benchmark_summary, execute_jev_assisted
from mcp_toolcall_lab.jev_backend import FixtureBackend

_FAMILIES = ("ironmate", "wikipedia", "mock", "none")


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("benchmark fixture must be a JSON list")
    return data


def _backend_for_case(case: dict[str, Any]) -> FixtureBackend:
    needs = float(case["needs_probability"])
    fixtures: dict[str, dict[str, Any]] = {
        "needs_tool": {"type": "noul", "noul": needs}
    }
    if needs >= 0.5:
        family = str(case["chosen_family"])
        confidence = float(case["family_confidence"])
        remainder = (1.0 - confidence) / (len(_FAMILIES) - 1)
        fixtures["tool_family"] = {
            "type": "choice",
            "choice": family,
            "confidence": confidence,
            "probabilities": {
                name: confidence if name == family else remainder
                for name in _FAMILIES
            },
        }
    return FixtureBackend(fixtures=fixtures)


def run_offline_comparison(
    cases: list[dict[str, Any]],
    *,
    ironmate_client: IronmateClient,
    fallback: Callable[[Any], Any],
    threshold: float = 0.75,
) -> dict[str, Any]:
    """Compare always-LLM baseline call count with deterministic assisted policy.

    This measures the policy/harness only. Fixture probabilities are labels chosen
    for repeatability; they are not evidence of live Jev model quality.
    """
    rows: list[dict[str, Any]] = []
    for case in cases:
        result = execute_jev_assisted(
            _backend_for_case(case),
            case["state"],
            ironmate_client=ironmate_client,
            ironmate_tool=str(case.get("ironmate_tool", "search_repository_metadata")),
            ironmate_arguments=dict(case.get("ironmate_arguments", {})),
            fallback=fallback,
            threshold=threshold,
            meta={"benchmark_case": case["id"]},
        )
        tool_success = None
        if result.tool_called:
            tool_success = result.execution_fallback_reason is None
        row = benchmark_row(
            result,
            expected_tool_family=str(case["expected_tool_family"]),
            tool_success=tool_success,
        )
        row["id"] = case["id"]
        rows.append(row)

    assisted = benchmark_summary(rows)
    baseline_llm_calls = len(cases)
    assisted_llm_calls = sum(bool(row["llm_used"]) for row in rows)
    return {
        "baseline": {"llm_calls": baseline_llm_calls},
        "assisted": {**assisted, "llm_calls": assisted_llm_calls},
        "delta": {
            "llm_calls": assisted_llm_calls - baseline_llm_calls,
            "llm_calls_avoided": baseline_llm_calls - assisted_llm_calls,
            "correctness_regressions": assisted["wrong_routes"],
        },
        "rows": rows,
    }
