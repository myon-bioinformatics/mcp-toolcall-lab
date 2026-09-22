from __future__ import annotations

from pathlib import Path

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_benchmark import load_cases, run_offline_comparison


FIXTURE = Path(__file__).parents[1] / "fixtures" / "jev_assisted" / "benchmark_cases.json"


class FakeSession:
    def __init__(self):
        self.calls = []

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return {"result": {"tools": []}}

    def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        return {"result": {"ok": True}}


def test_offline_comparison_has_mixed_realistic_cases():
    cases = load_cases(FIXTURE)
    assert len(cases) == 12
    assert {case["expected_tool_family"] for case in cases} == {
        "ironmate", "wikipedia", "mock", "none"
    }
    assert any("Ironmate" in case["state"] for case in cases)
    assert any("Wikipedia" in case["state"] for case in cases)
    assert any(any(ord(ch) > 127 for ch in case["state"]) for case in cases)


def test_offline_comparison_quantifies_baseline_vs_assisted_policy():
    session = FakeSession()
    fallback_calls = []
    report = run_offline_comparison(
        load_cases(FIXTURE),
        ironmate_client=IronmateClient(session),
        fallback=lambda state: fallback_calls.append(state) or {"fallback": state},
    )

    assert report["prob_sources"] == ["fixture"]
    assert report["baseline"]["llm_calls"] == 12
    assert report["assisted"]["llm_calls"] == 6
    assert report["delta"]["llm_calls"] == -6
    assert report["delta"]["llm_calls_avoided"] == 6
    assert report["delta"]["correctness_regressions"] == 0

    assert report["assisted"]["policy_fallbacks"] == 1
    assert report["assisted"]["execution_fallbacks"] == 0
    assert report["assisted"]["out_of_scope_family"] == 5
    assert report["assisted"]["tool_attempts"] == 3
    assert report["assisted"]["tool_successes"] == 3
    assert len(session.calls) == 3
    assert len(fallback_calls) == 6


def test_report_rows_keep_case_ids_and_expected_families():
    report = run_offline_comparison(
        load_cases(FIXTURE),
        ironmate_client=IronmateClient(FakeSession()),
        fallback=lambda state: state,
    )
    rows = report["rows"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["expected_tool_family"] for row in rows} == {
        "ironmate", "wikipedia", "mock", "none"
    }
