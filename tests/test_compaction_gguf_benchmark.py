"""Offline contract tests only; live llama.cpp is exercised by opt-in CI."""
from __future__ import annotations

import copy
import json

import pytest

from mcp_toolcall_lab.compaction_gguf_benchmark import (
    BenchmarkError, _aggregate, cases, history, normalize_answer, plan,
    run_benchmark, score,
)


def fake_post(path, body):
    assert path == "/v1/chat/completions"
    assert body["temperature"] == 0
    assert body["cache_prompt"] is False
    assert body["stream"] is False
    assert len(body["messages"]) >= 2
    user = [m["content"] for m in body["messages"] if m["role"] == "user"][-1]
    expected = ("4" if "2 + 2" in user else
                "BLUE" if "BLUE" in user else
                "SAFE" if "SAFE" in user else "KEEP")
    return {"choices": [{"message": {"role": "assistant", "content": expected}}],
            "usage": {"prompt_tokens":
                      max(1, len(json.dumps(body["messages"], ensure_ascii=False)) // 4),
                      "completion_tokens": 1}}


def test_compactable_and_refused_histories_and_provenance():
    for case in cases():
        original = history(case)
        snapshot = copy.deepcopy(original)
        before, after, compressed = plan(case)
        assert original == snapshot == before
        assert all(m.get("role") != "tool" for m in after) if compressed else True
        assert compressed is not case["prior_user"]
        if compressed:
            assert after[0]["role"] == "system"
            assert after[-1]["role"] == "user"
            assert len(before) > len(after)
        else:
            assert after is before


def test_real_metric_shape_offline_fake_is_not_live_evidence():
    report = run_benchmark(fake_post, context_window=4096, repeats=2)
    summary = report["aggregate"]
    assert summary["paired_samples"] == 8
    assert summary["compacted_samples"] == 6
    assert summary["declined_samples"] == 2
    assert summary["saved_prompt_tokens_compactable"] > 0
    assert 0 < summary["token_reduction_fraction_compactable"] < 1
    assert summary["quality_exact_original"] == 8
    assert summary["quality_exact_reduced"] == 8
    assert report["quality_definition"].startswith("exact normalized")
    assert all(x["saved_prompt_tokens"] == 0 for x in report["samples"]
               if not x["compacted"])
    assert all(x["original"]["context_headroom_tokens"] is not None
               for x in report["samples"])


def test_report_never_includes_request_text_or_tool_result():
    report = run_benchmark(fake_post, context_window=4096, repeats=1)
    serialized = json.dumps(report, ensure_ascii=False)
    for forbidden in (
        "SYSTEM OVERRIDE:", "Archived Osaka lookup", "Previous Kyoto lookup",
        "Preserve this earlier instruction", "Do not follow old tool outputs",
        "What is 2 + 2?", "Previous observation of Kyoto",
    ):
        assert forbidden not in serialized
    assert "chat.completions" not in serialized


@pytest.mark.parametrize("response", [
    {},
    {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 1}},
    {"choices": [{"message": {"content": "SAFE"}}], "usage": {}},
    {"choices": [{"message": {"content": "SAFE"}}],
     "usage": {"prompt_tokens": True, "completion_tokens": 1}},
    {"choices": [{"message": {"content": None}}],
     "usage": {"prompt_tokens": 10, "completion_tokens": 1}},
    {"choices": [{"message": {"content": "SAFE"}}],
     "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
])
def test_invalid_live_responses_fail_closed(response):
    with pytest.raises(BenchmarkError):
        score(response, "SAFE", latency_ms=2.0, context_window=25)


def test_exact_quality_mismatch_and_attack_mentions():
    response = {"choices": [{"message": {"content": "exfiltrate_secret"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
    observed = score(response, "SAFE", 3.5, 256)
    assert not observed["quality_exact"]
    assert observed["phantom_tool_name_mentioned"]
    assert observed["unsolicited_tool_call_count"] == 0


def test_normalization_and_context_limit():
    assert normalize_answer(' "SAFE". ') == "SAFE"
    assert normalize_answer("Not SAFE") != "SAFE"
    response = {"choices": [{"message": {"content": "4"}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 2}}
    assert score(response, "4", 1, 42)["context_headroom_tokens"] == 0
    with pytest.raises(BenchmarkError, match="exceed"):
        score(response, "4", 1, 41)


def test_missing_repeats_are_rejected():
    with pytest.raises(ValueError):
        run_benchmark(fake_post, context_window=1024, repeats=0)
