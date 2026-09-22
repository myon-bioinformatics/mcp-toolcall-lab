from __future__ import annotations

import json

from mcp_toolcall_lab.jev_backend import FixtureBackend
from mcp_toolcall_lab.jev_live_benchmark import run_backend_routing_benchmark, urllib_json_post


def backend():
    return FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.98},
        "tool_family": {
            "type": "choice",
            "choice": "ironmate",
            "confidence": 0.95,
            "probabilities": {
                "ironmate": 0.95,
                "wikipedia": 0.02,
                "mock": 0.02,
                "none": 0.01,
            },
        },
    })


def test_measured_backend_report_keeps_provenance_and_correctness():
    report = run_backend_routing_benchmark([
        {"id": "a", "state": "repository metadata", "expected_tool_family": "ironmate"},
        {"id": "b", "state": "also metadata", "expected_tool_family": "wikipedia"},
    ], backend())

    assert report["mode"] == "measured-backend-routing"
    assert report["prob_sources"] == ["fixture"]
    assert report["cases"] == 2
    assert report["routed"] == 2
    assert report["fallbacks"] == 0
    assert report["wrong_routes"] == 1
    assert report["selector_calls_skipped_by_policy"] == 2
    assert report["confusion_matrix"]["routed-correct"] == 1
    assert report["confusion_matrix"]["routed-wrong"] == 1
    assert report["calibration"]["fixture"]["n"] == 2


def test_low_confidence_is_fallback_not_avoided():
    low = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.6},
        "tool_family": {
            "type": "choice",
            "choice": "ironmate",
            "confidence": 0.6,
            "probabilities": {
                "ironmate": 0.6,
                "wikipedia": 0.14,
                "mock": 0.13,
                "none": 0.13,
            },
        },
    })
    report = run_backend_routing_benchmark([
        {"id": "a", "state": "ambiguous", "expected_tool_family": "ironmate"},
    ], low)
    assert report["fallbacks"] == 1
    assert report["selector_calls_skipped_by_policy"] == 0
    assert report["wrong_routes"] == 0


def test_urllib_json_post_is_injectable(monkeypatch):
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return b'{"choices": []}'
    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data)
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = urllib_json_post("http://cpu-llm:8080/v1/chat/completions", {"model": "x"})
    assert result == {"choices": []}
    assert seen == {
        "url": "http://cpu-llm:8080/v1/chat/completions",
        "timeout": 30.0,
        "body": {"model": "x"},
    }
