from __future__ import annotations

from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_assisted import execute_jev_assisted
from mcp_toolcall_lab.jev_backend import FixtureBackend


class FakeSession:
    def call_tool(self, name, arguments, **kwargs):
        return {"result": {"ok": True}}


def backend(family: str):
    return FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.98},
        "tool_family": {
            "type": "choice",
            "choice": family,
            "confidence": 0.96,
            "probabilities": {
                name: 0.96 if name == family else (0.04 / 3)
                for name in ("ironmate", "wikipedia", "mock", "none")
            },
        },
    })


def test_high_confidence_wikipedia_can_skip_selector_llm():
    calls = []
    result = execute_jev_assisted(
        backend("wikipedia"),
        "Wikipedia BLEACH",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "fallback",
        direct_calls={"wikipedia": lambda: calls.append("wiki") or {"title": "BLEACH"}},
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert result.result == {"title": "BLEACH"}
    assert calls == ["wiki"]


def test_high_confidence_mock_can_skip_selector_llm():
    result = execute_jev_assisted(
        backend("mock"),
        "exercise mock",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "fallback",
        direct_calls={"mock": lambda: [{"source": "mock"}]},
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert result.result == [{"source": "mock"}]


def test_unwired_family_still_uses_existing_flow():
    result = execute_jev_assisted(
        backend("wikipedia"),
        "Wikipedia",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
    )
    assert result.llm_used is True
    assert result.tool_called is False
    assert result.result == "existing-flow"


def test_direct_family_failure_degrades_to_existing_flow():
    def fail():
        raise RuntimeError("down")

    result = execute_jev_assisted(
        backend("wikipedia"),
        "Wikipedia",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
        direct_calls={"wikipedia": fail},
    )
    assert result.llm_used is True
    assert result.tool_called is True
    assert result.result == "existing-flow"
    assert result.execution_fallback_reason == "wikipedia_call_error"


def test_arbitrary_family_key_cannot_bypass_supported_family_gate():
    calls = []
    result = execute_jev_assisted(
        backend("mock"),
        "mock",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
        direct_calls={"ironmate": lambda: calls.append("bad")},
    )
    assert result.llm_used is True
    assert calls == []
