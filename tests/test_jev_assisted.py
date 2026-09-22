from __future__ import annotations

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_assisted import benchmark_row, benchmark_summary, execute_jev_assisted
from mcp_toolcall_lab.jev_backend import FixtureBackend


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


def backend(needs: float, family: str = "ironmate", confidence: float = .95):
    fixtures = {"needs_tool": {"type": "noul", "noul": needs}}
    if needs >= .5:
        fixtures["tool_family"] = {
            "type": "choice",
            "choice": family,
            "confidence": confidence,
            "probabilities": {
                "ironmate": confidence if family == "ironmate" else .01,
                "wikipedia": confidence if family == "wikipedia" else .01,
                "mock": confidence if family == "mock" else .01,
                "none": confidence if family == "none" else .01,
            },
        }
    return FixtureBackend(fixtures=fixtures)


def test_high_confidence_ironmate_skips_llm_and_calls_mcp():
    session = FakeSession()
    fallback_calls = []
    result = execute_jev_assisted(
        backend(.98),
        "find repository metadata",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "markdown"},
        fallback=lambda state: fallback_calls.append(state),
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert result.result == {"result": {"ok": True}}
    assert fallback_calls == []
    assert session.calls[0][0] == "search_repository_metadata"
    assert session.calls[0][2]["meta"]["source"] == "jev-router"


def test_low_confidence_ironmate_preserves_existing_fallback():
    session = FakeSession()
    result = execute_jev_assisted(
        backend(.6, confidence=.6),
        "ambiguous",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "x"},
        fallback=lambda state: {"llm": state},
    )
    assert result.llm_used is True
    assert result.tool_called is False
    assert result.result == {"llm": "ambiguous"}
    assert result.decision.fallback_reason == "low_confidence"
    assert session.calls == []


def test_non_ironmate_family_stays_on_existing_flow_in_first_slice():
    result = execute_jev_assisted(
        backend(.98, family="wikipedia"),
        "Wikipedia please",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
    )
    assert result.llm_used is True
    assert result.result == "existing-flow"


def test_high_confidence_no_tool_avoids_llm_without_calling_tool():
    session = FakeSession()
    result = execute_jev_assisted(
        backend(.02),
        "hello",
        ironmate_client=IronmateClient(session),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "should-not-run",
    )
    assert result.llm_used is False
    assert result.tool_called is False
    assert result.result is None
    assert session.calls == []


def test_benchmark_surface_counts_avoided_wrong_fallback_and_success():
    session = FakeSession()
    direct = execute_jev_assisted(
        backend(.98),
        "metadata",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "markdown"},
        fallback=lambda state: "fallback",
    )
    fallback = execute_jev_assisted(
        backend(.6, confidence=.6),
        "ambiguous",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "x"},
        fallback=lambda state: "fallback",
    )
    rows = [
        benchmark_row(direct, expected_tool_family="ironmate", tool_success=True),
        benchmark_row(fallback, expected_tool_family="ironmate"),
    ]
    summary = benchmark_summary(rows)
    assert summary["cases"] == 2
    assert summary["llm_calls_avoided"] == 1
    assert summary["wrong_routes"] == 0
    assert summary["fallbacks"] == 1
    assert summary["tool_successes"] == 1
    assert summary["tool_attempts"] == 1
