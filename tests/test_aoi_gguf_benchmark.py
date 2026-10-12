"""Aoi labels are historical published outcomes, not independent confirmation."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from mcp_toolcall_lab.aoi_gguf_benchmark import (
    ATTACKS, EXPECTED_CODES, REFUSED, BenchmarkError, _as_digit,
    catalog, run, scenario,
)


def _synthetic_aoi_files(tmp_path: Path) -> tuple[Path, Path]:
    # Small synthetic source only. Live Actions checks out the pinned Aoi repo.
    index = tmp_path / "index.md"
    propositions = tmp_path / "propositions.toml"
    index.write_text(
        "# Published verdicts\n"
        + "".join(
            f"| {pid} | [all] test => true | mostly | 1.00 (8) | "
            f"**{code}** archived outcome |\n"
            for pid, code in EXPECTED_CODES.items()
        ), encoding="utf-8")
    propositions.write_text("".join(
        "[[proposition]]\n"
        f'id = "{pid}"\n'
        f'statement = "Test statement {pid}"\n'
        for pid in EXPECTED_CODES
    ), encoding="utf-8")
    return index, propositions


def _data(tmp_path):
    return catalog(*_synthetic_aoi_files(tmp_path), strict=False)


def _fake_post(path, payload):
    assert path == "/v1/chat/completions"
    assert payload["temperature"] == 0
    assert payload["cache_prompt"] is False
    assert payload["stream"] is False
    assert payload["response_format"]["type"] == "json_schema"
    messages = payload["messages"]
    user = [m["content"] for m in messages if m["role"] == "user"][-1]
    pid = re.search(r"TARGET_ID: (P\d+)", user).group(1)
    assert f"ID={pid} | overall_exit={EXPECTED_CODES[pid]}" in user
    answer = str(EXPECTED_CODES[pid])
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps({"exit_code": int(answer)})}}],
        "usage": {
            "prompt_tokens": max(1, len(json.dumps(messages, ensure_ascii=False)) // 3),
            "completion_tokens": 1,
        },
    }


def test_aoi_verdict_reader_and_consistent_source_identity(tmp_path):
    data = _data(tmp_path)
    assert data["registered_count"] == len(EXPECTED_CODES) == 12
    assert data["verdicts"] == EXPECTED_CODES
    assert all(len(d) == 64 for d in [data["index_sha256"], data["propositions_sha256"]])
    with pytest.raises(BenchmarkError, match="count"):
        catalog(*_synthetic_aoi_files(tmp_path))


def test_broken_or_missing_source_rejected(tmp_path):
    index, propositions = _synthetic_aoi_files(tmp_path)
    index.write_text(index.read_text().replace("| P2 |", "| P1 |"), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="duplicate"):
        catalog(index, propositions, strict=False)
    index, propositions = _synthetic_aoi_files(tmp_path)
    index.write_text(index.read_text().replace("**1** archived outcome", "**0** archived outcome", 1),
                     encoding="utf-8")
    with pytest.raises(BenchmarkError, match="changed for P2"):
        catalog(index, propositions, strict=False)
    index, propositions = _synthetic_aoi_files(tmp_path)
    propositions.write_text(propositions.read_text().replace('id = "P96"', 'id = "P997"'),
                            encoding="utf-8")
    with pytest.raises(BenchmarkError, match="do not match"):
        catalog(index, propositions, strict=False)


def test_compaction_preserves_real_user_turns_and_typed_tool_boundary(tmp_path):
    data = _data(tmp_path)
    for pid in EXPECTED_CODES:
        old, new, changed = scenario(data, pid)
        untouched = copy.deepcopy(old)
        assert old == untouched
        assert new[-1] == old[-1]
        assert changed is (pid not in REFUSED)
        assert len([m for m in old if m["role"] == "tool"]) == 3
        if changed:
            assert len(new) == 2
            assert all(m["role"] != "tool" for m in new)
        else:
            assert new is old
        if pid in ATTACKS:
            assert "Ignore the TARGET_ID" in json.dumps(old)
            assert "Ignore the TARGET_ID" not in json.dumps(new)


def test_paired_metrics_and_secret_free_artifact(tmp_path):
    data = _data(tmp_path)
    result = run(data, _fake_post, context_window=2048, repeats=2,
                 model_sha256="f" * 64)
    a = result["aggregate"]
    assert a["paired_samples"] == 24
    assert a["compacted_samples"] == 20
    assert a["declined_samples"] == 4
    assert a["quality_exact_original"] == a["quality_exact_reduced"] == 24
    assert a["quality_exact_by_compaction_status"]["refused"]["n"] == 4
    assert a["saved_prompt_tokens_compactable"] > 0
    assert 0 < a["token_reduction_fraction_compactable"] < 1
    assert a["attack_case_count"] == 4
    assert result["aoi_source_commit"]
    assert len(result["samples"]) == 24
    assert all(isinstance(s["original"]["predicted_exit_code"], int)
               for s in result["samples"])
    published = json.dumps(result, ensure_ascii=False)
    for forbidden in ("SYSTEM OVERRIDE", "Ignore the TARGET_ID",
                      "Test statement", "Preserve earlier user constraints",
                      "Old public Aoi record:", "read the Aoi adjudication"):
        assert forbidden.lower() not in published.lower()


def test_invalid_predictions_never_rewritten_as_valid_digit():
    for reply in ("exit=1", "4\nexplanation", "0 1", "unknown", "",
                  "{\"exit_code\": true}", "{\"exit_code\": 7}",
                  "{\"exit_code\": 1, \"exit_code\": 2}",
                  "{\"exit_code\": \"3\"}", "{\"exit_code\": 3, \"extra\": 1}"):
        assert _as_digit({"choices": [{"message": {"content": reply}}]}) is None
    assert _as_digit({"choices": [{"message": {"content": ' {"exit_code":3} '}}]}) == 3
    assert _as_digit({}) is None


def test_source_injection_cannot_change_latest_user_reference(tmp_path):
    data = _data(tmp_path)
    for pid in ATTACKS:
        original, reduced, compacted = scenario(data, pid)
        assert compacted
        assert any(m["role"] == "tool" and "role: system" in m["content"]
                   for m in original)
        assert all("role: system" not in m.get("content", "") for m in reduced)
        assert f"TARGET_ID: {pid}" in reduced[-1]["content"]


def test_repeats_and_context_fail_closed(tmp_path):
    data = _data(tmp_path)
    with pytest.raises(ValueError):
        run(data, _fake_post, context_window=2048, repeats=0, model_sha256="f"*64)
    with pytest.raises(BenchmarkError, match="context"):
        run(data, _fake_post, context_window=128, repeats=1, model_sha256="f"*64)
