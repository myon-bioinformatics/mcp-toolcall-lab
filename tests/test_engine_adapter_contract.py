"""Execution receipts distinguish replay, approximation and unavailable inference."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mcp_toolcall_lab.adapters.engine_adapter import (
    distill_engine_adapter, execute_via_adapter, export_adapter_cache,
    extract_engine_spec, load_adapter_cache,
)


@pytest.fixture
def adapter():
    return distill_engine_adapter(
        extract_engine_spec(engine_name="declared-gpu", repo_or_image="example/model",
                            device_requirement="gpu"),
        {"known": {"evaluation_score": 12, "best_move": "7g7f"},
         "default": {"evaluation_score": -3, "confidence": 0.7}},
    )


def test_legacy_cache_and_known_call_keep_values_with_truthful_receipt(tmp_path, adapter):
    loaded = load_adapter_cache(export_adapter_cache(adapter, tmp_path / "cache.json"))
    result = execute_via_adapter(loaded, {"position": "known"})
    assert result["result_contract_version"] == "2.0"
    assert result["status"] == "ok"
    assert result["evaluation"] == {"evaluation_score": 12, "best_move": "7g7f"}
    assert result["cache_hit"] is True
    assert result["matched_key"] == "known"
    assert result["execution_mode"] == "cached_observation"
    assert result["device_requirement"] == "gpu"
    assert result["device_used"] == "unknown"
    assert result["model_inference_performed"] is False
    assert result["upstream_bypassed"] is True
    assert result["execution_ms"] >= 0
    assert "error" not in result


@pytest.mark.parametrize("observations", [{}, {"default": {"evaluation_score": 5}}])
def test_unknown_input_never_succeeds_silently(adapter, observations):
    adapter = replace(adapter, evaluation_features=observations)
    result = execute_via_adapter(adapter, {"query": "unseen"})
    assert result["status"] == "cache_miss"
    assert result["error"]["code"] == "cache_miss"
    assert result["execution_mode"] == "unsupported"
    assert result["cache_hit"] is False
    assert result["evaluation"] is None


def test_opt_in_default_is_deterministic_unvalidated_constant(adapter):
    results = [execute_via_adapter(adapter, {"query": query}, on_miss="stored_default")
               for query in ("unseen-a", "unseen-b", "unseen-a")]
    for result in results:
        assert result["status"] == "ok"
        assert result["execution_mode"] == "approximation"
        assert result["cache_hit"] is False
        assert result["evaluation"] == {"evaluation_score": -3, "confidence": 0.7}
        assert result["approximation"]["method"] == "stored_default_constant_v1"
        assert result["approximation"]["limitations"]
        assert result["quality"] == {"accuracy": None, "error_bound": None, "calibrated": False}
        assert result["device_used"] == "unknown"
        assert result["model_inference_performed"] is False
    # Opt-in never replaces a known observation.
    known = execute_via_adapter(adapter, {"query": "known"}, on_miss="stored_default")
    assert known["cache_hit"] is True
    assert known["evaluation"]["evaluation_score"] == 12


def test_missing_approximation_reference_is_a_miss(adapter):
    result = execute_via_adapter(replace(adapter, evaluation_features={}),
                                 {"query": "unseen"}, on_miss="stored_default")
    assert result["status"] == "cache_miss"
    assert result["evaluation"] is None


@pytest.mark.parametrize("value", [0, False, {}, [], None, ""])
def test_falsy_observations_are_hits_not_fallbacks(adapter, value):
    adapter = replace(adapter, evaluation_features={"known": value, "default": 999})
    result = execute_via_adapter(adapter, {"query": "known"})
    assert result["cache_hit"] is True
    assert result["evaluation"] == value


@pytest.mark.parametrize("selector", [{}, {"query": "default"}, {"position": "default"}])
def test_explicit_default_selector_is_an_observation(adapter, selector):
    result = execute_via_adapter(adapter, selector)
    assert result["execution_mode"] == "cached_observation"
    assert result["cache_hit"] is True
    assert result["matched_key"] == "default"


@pytest.mark.parametrize("input_case", [None, [], {"prompt": "run model"}, {"query": ""},
    {"query": 0}, {"position": False}, {"query": None}, {"query": ["known"]},
    {"query": "known", "temperature": 1}, {"query": "known", "position": "known"}])
def test_unsupported_requests_do_not_replay_or_run_a_model(adapter, input_case):
    result = execute_via_adapter(adapter, input_case, on_miss="stored_default")
    assert result["status"] == "unsupported"
    assert result["error"]["code"] == "unsupported_input"
    assert result["evaluation"] is None
    assert result["device_used"] == "unknown"
    assert result["model_inference_performed"] is False


def test_receipts_do_not_expose_mutable_cached_evaluations(adapter):
    result = execute_via_adapter(adapter, {"query": "known"})
    result["evaluation"]["best_move"] = "mutated"
    assert execute_via_adapter(adapter, {"query": "known"})["evaluation"]["best_move"] == "7g7f"


@pytest.mark.parametrize("on_miss", ["unsupported", "stored_default"])
def test_expired_snapshot_blocks_replay_and_approximation(tmp_path, adapter, on_miss):
    old = replace(adapter, created_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    old = load_adapter_cache(export_adapter_cache(old, tmp_path / "old.json"))
    for key in ("known", "unseen"):
        result = execute_via_adapter(old, {"query": key}, on_miss=on_miss, max_age_seconds=60)
        assert result["status"] == "cache_miss"
        assert result["error"]["code"] == "cache_expired"
        assert result["evaluation"] is None
    assert execute_via_adapter(old, {"query": "known"})["cache_hit"] is True


def test_fresh_snapshot_and_zero_ttl(adapter):
    assert execute_via_adapter(adapter, {"query": "known"}, max_age_seconds=60)["cache_hit"] is True
    assert execute_via_adapter(adapter, {}, max_age_seconds=0)["error"]["code"] == "cache_expired"


@pytest.mark.parametrize("timestamp", ["invalid", "2026-01-01T00:00:00", None,
    "9999-01-01T00:00:00+00:00"])
def test_invalid_or_future_timestamp_fails_closed_with_ttl(adapter, timestamp):
    result = execute_via_adapter(replace(adapter, created_at=timestamp), {}, max_age_seconds=60)
    assert result["status"] == "unsupported"
    assert result["error"]["code"] == "invalid_cache_timestamp"


@pytest.mark.parametrize("ttl", [-1, float("nan"), float("inf"), True, "60"])
def test_invalid_policy_rejected(adapter, ttl):
    with pytest.raises(ValueError, match="max_age_seconds"):
        execute_via_adapter(adapter, {}, max_age_seconds=ttl)


def test_large_finite_integer_ttl_is_supported(adapter):
    assert execute_via_adapter(adapter, {"query": "known"}, max_age_seconds=10**400)["cache_hit"] is True


def test_unknown_miss_policy_rejected(adapter):
    with pytest.raises(ValueError, match="on_miss"):
        execute_via_adapter(adapter, {}, on_miss="invent_score")


@pytest.mark.parametrize("content", ["{broken", "[]", "null", "42"])
def test_corrupt_or_non_object_cache_rejected(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_adapter_cache(path)


@pytest.mark.parametrize("field,value", [("evaluation_features", []), ("source_spec", None),
    ("runtime_bounds", 0), ("provenance", []), ("created_at", None), ("adapter_id", "")])
def test_malformed_trusted_migration_rejected(tmp_path, adapter, field, value):
    path = export_adapter_cache(adapter, tmp_path / "cache.json")
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=field):
        load_adapter_cache(path, verify_checksum=False)


def test_direct_python_path_works_without_site_packages():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    code = """
from mcp_toolcall_lab.adapters.engine_adapter import extract_engine_spec, distill_engine_adapter, execute_via_adapter
adapter = distill_engine_adapter(extract_engine_spec(engine_name='offline', repo_or_image='local'), {'a': 7})
assert execute_via_adapter(adapter, {'query': 'a'})['evaluation'] == 7
assert execute_via_adapter(adapter, {'query': 'b'})['status'] == 'cache_miss'
"""
    subprocess.run([sys.executable, "-S", "-c", code], env=env, check=True, timeout=10)
