"""Upstream distillation engine adapter: receipts, whole-cache integrity and offline fixture selection."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab import jev_shim, prompt_experiment
from mcp_toolcall_lab.adapters.engine_adapter import (
    DistilledAdapter, EngineSpec, distill_engine_adapter, execute_via_adapter, export_adapter_cache, extract_engine_spec, load_adapter_cache,
)
from mcp_toolcall_lab.fixture_selection import fixture_paths


# ═══ · engine adapter ════════════════════════════════════════════════════════════════════════════════════
# Tests for Upstream Distillation Engine Adapter (src/mcp_toolcall_lab/adapters/engine_adapter.py).

def test_extract_engine_spec_huggingface_valid() -> None:
    spec = extract_engine_spec(
        engine_name="yaneuraou-nnue",
        source_type="huggingface",
        repo_or_image="shogi-ai/yaneuraou-kppm",
        revision_or_tag="v1.0.0",
        files=("nnue.bin", "config.json"),
        parameter_count=15000000,
        device_requirement="cpu",
    )
    assert spec.engine_name == "yaneuraou-nnue"
    assert spec.source_type == "huggingface"
    assert spec.repo_or_image == "shogi-ai/yaneuraou-kppm"
    assert spec.revision_or_tag == "v1.0.0"
    assert spec.files == ("nnue.bin", "config.json")
    assert spec.parameter_count == 15000000
    assert spec.device_requirement == "cpu"


def test_extract_engine_spec_docker_valid() -> None:
    spec = extract_engine_spec(
        engine_name="shogi-engine-docker",
        source_type="docker",
        repo_or_image="yaneuraou/shogi-engine",
        revision_or_tag="latest",
        device_requirement="cpu",
    )
    assert spec.source_type == "docker"
    assert spec.repo_or_image == "yaneuraou/shogi-engine"
    assert spec.revision_or_tag == "latest"


def test_extract_engine_spec_validations() -> None:
    with pytest.raises(ValueError, match="engine_name must be non-empty"):
        extract_engine_spec(engine_name="", repo_or_image="repo")

    with pytest.raises(ValueError, match="source_type must be one of"):
        extract_engine_spec(engine_name="test", source_type="unsupported", repo_or_image="repo")

    with pytest.raises(ValueError, match="repo_or_image must be non-empty"):
        extract_engine_spec(engine_name="test", repo_or_image="  ")

    with pytest.raises(ValueError, match="device_requirement must be 'cpu' or 'gpu'"):
        extract_engine_spec(engine_name="test", repo_or_image="repo", device_requirement="tpu")

    with pytest.raises(ValueError, match="parameter_count must be a non-negative integer"):
        extract_engine_spec(engine_name="test", repo_or_image="repo", parameter_count=-5)


def test_distill_engine_adapter() -> None:
    spec = extract_engine_spec(
        engine_name="smollm2-135m-shogi",
        source_type="huggingface",
        repo_or_image="bartowski/SmolLM2-135M-Instruct-GGUF",
        revision_or_tag="09816a",
    )
    obs = {
        "start_position": {"evaluation_score": 120.0, "best_move": "7g7f"},
        "tsume_position": {"evaluation_score": 30000.0, "best_move": "5a5b"},
        "default": {"evaluation_score": 0.0, "best_move": "pass"},
    }

    adapter = distill_engine_adapter(spec, obs, adapter_id="test-shogi-adapter")
    assert adapter.adapter_id == "test-shogi-adapter"
    assert adapter.engine_name == "smollm2-135m-shogi"
    assert adapter.schema_version == "1.0"
    assert adapter.source_spec["repo_or_image"] == "bartowski/SmolLM2-135M-Instruct-GGUF"
    assert len(adapter.sha256) == 64
    assert "https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF" in adapter.provenance["source_url"]


def test_export_and_load_adapter_cache(tmp_path: Path) -> None:
    spec = extract_engine_spec(
        engine_name="yaneuraou-kppm",
        source_type="huggingface",
        repo_or_image="yaneuraou/kppm-weights",
        revision_or_tag="main",
    )
    obs = {
        "position_1": {"evaluation_score": 250.0},
        "default": {"evaluation_score": 0.0},
    }
    adapter = distill_engine_adapter(spec, obs)

    cache_file = tmp_path / "adapters" / "yaneuraou_adapter.json"
    exported_path = export_adapter_cache(adapter, cache_file)
    assert exported_path.is_file()

    loaded = load_adapter_cache(cache_file, verify_checksum=True)
    assert loaded.adapter_id == adapter.adapter_id
    assert loaded.sha256 == adapter.sha256
    assert loaded.evaluation_features == obs


def test_load_adapter_cache_checksum_mismatch(tmp_path: Path) -> None:
    spec = extract_engine_spec(
        engine_name="test-engine",
        source_type="local",
        repo_or_image="/models/test",
    )
    obs = {"default": {"evaluation_score": 10.0}}
    adapter = distill_engine_adapter(spec, obs)

    cache_file = tmp_path / "adapter.json"
    export_adapter_cache(adapter, cache_file)

    # Tamper with the JSON payload
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    data["evaluation_features"]["default"]["evaluation_score"] = 9999.0
    cache_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="Adapter checksum mismatch"):
        load_adapter_cache(cache_file, verify_checksum=True)


def test_execute_via_adapter() -> None:
    spec = extract_engine_spec(
        engine_name="yaneuraou-distilled",
        source_type="docker",
        repo_or_image="shogi/yaneuraou",
        revision_or_tag="v2026",
    )
    obs = {
        "startpos": {"evaluation_score": 80.0, "best_move": "2f2e"},
        "default": {"evaluation_score": 0.0, "best_move": "none"},
    }
    adapter = distill_engine_adapter(spec, obs)

    # Execute with matching key
    res1 = execute_via_adapter(adapter, {"position": "startpos"})
    assert res1["status"] == "ok"
    assert res1["upstream_bypassed"] is True
    assert res1["evaluation"]["best_move"] == "2f2e"
    assert res1["device_used"] == "unknown"
    assert res1["device_requirement"] == "cpu"
    assert res1["execution_mode"] == "cached_observation"

    # Explicitly opt into the legacy default fallback as an approximation.
    res2 = execute_via_adapter(adapter, {"position": "unknown_pos"}, on_miss="stored_default")
    assert res2["status"] == "ok"
    assert res2["execution_mode"] == "approximation"
    assert res2["cache_hit"] is False
    assert res2["evaluation"]["best_move"] == "none"


# ═══ · engine adapter contract ═══════════════════════════════════════════════════════════════════════════
# Execution receipts distinguish replay, approximation and unavailable inference.

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


# ═══ · engine adapter integrity ══════════════════════════════════════════════════════════════════════════
# Whole-cache integrity, JSON normalization, and legacy API contracts.

@pytest.fixture
def adapter_engine_adapter_integrity():
    return distill_engine_adapter(
        extract_engine_spec(
            engine_name="将棋-engine", source_type="local",
            repo_or_image="/models/nnue", files=("weights.bin", "config.json"),
            metadata={"nested": {"sha256": "model-digest", "label": "評価"}},
        ),
        {"default": {"evaluation_score": 10.0, "best_move": "7g7f"}},
    )


def _digest(payload):
    # Independent public-format oracle: only the top-level digest is excluded.
    payload = dict(payload)
    payload.pop("sha256", None)
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("field,value", [
    ("adapter_id", "forged-adapter"),
    ("engine_name", "forged-engine"),
    ("runtime_bounds", {"max_memory_mb": 999999}),
    ("provenance", {"source_url": "https://forged.example/model"}),
    ("created_at", "2000-01-01T00:00:00+00:00"),
    ("source_spec", {"repo_or_image": "/forged/model"}),
    ("evaluation_features", {"default": {"evaluation_score": 9999.0}}),
    ("sha256", "0" * 64),
])
def test_each_cache_field_tamper_is_rejected(tmp_path, adapter_engine_adapter_integrity, field, value):
    path = export_adapter_cache(adapter_engine_adapter_integrity, tmp_path / "cache.json")
    data = _read(path)
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Adapter checksum mismatch"):
        load_adapter_cache(path)  # Verification remains enabled by default.


@pytest.mark.parametrize("field", ["runtime_bounds", "provenance", "created_at"])
def test_removed_field_is_rejected(tmp_path, adapter_engine_adapter_integrity, field):
    path = export_adapter_cache(adapter_engine_adapter_integrity, tmp_path / "cache.json")
    data = _read(path)
    del data[field]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Adapter checksum mismatch"):
        load_adapter_cache(path)


@pytest.mark.parametrize("change", ["schema", "extra", "nested_digest", "array_order"])
def test_complete_raw_payload_is_protected(tmp_path, adapter_engine_adapter_integrity, change):
    path = export_adapter_cache(adapter_engine_adapter_integrity, tmp_path / "cache.json")
    data = _read(path)
    if change == "schema":
        data["schema_version"] = "unsupported"
    elif change == "extra":
        data["extension"] = {"unrecognized": True}
    elif change == "nested_digest":
        data["source_spec"]["metadata"]["nested"]["sha256"] = "forged"
    else:
        data["source_spec"]["files"].reverse()
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch|Unsupported schema_version"):
        load_adapter_cache(path, verify_checksum=True)


def test_digest_matches_whole_exported_payload(tmp_path, adapter_engine_adapter_integrity):
    path = export_adapter_cache(adapter_engine_adapter_integrity, tmp_path / "cache.json")
    data = _read(path)
    assert adapter_engine_adapter_integrity.sha256 == data["sha256"] == _digest(data)
    assert data["schema_version"] == "1.0"
    assert set(data) == set(asdict(adapter_engine_adapter_integrity))
    loaded = load_adapter_cache(str(path), verify_checksum=True)
    assert asdict(loaded) == data
    result = execute_via_adapter(loaded, {})
    assert result["adapter_id"] == adapter_engine_adapter_integrity.adapter_id
    assert result["evaluation"]["best_move"] == "7g7f"


def test_formatting_and_recursive_key_order_do_not_change_digest(tmp_path, adapter_engine_adapter_integrity):
    def reorder(value):
        if isinstance(value, dict):
            return {k: reorder(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reorder(v) for v in value]
        return value

    path = export_adapter_cache(adapter_engine_adapter_integrity, tmp_path / "cache.json")
    data = _read(path)
    path.write_text(json.dumps(reorder(data), ensure_ascii=True, indent=4), encoding="utf-8")
    assert load_adapter_cache(path).sha256 == adapter_engine_adapter_integrity.sha256


def test_export_recomputes_digest_for_current_snapshot(tmp_path, adapter_engine_adapter_integrity):
    changed = replace(adapter_engine_adapter_integrity, engine_name="renamed-engine", sha256="stale")
    changed.runtime_bounds["max_memory_mb"] = 1024
    path = export_adapter_cache(changed, tmp_path / "cache.json")
    data = _read(path)
    assert data["sha256"] == _digest(data)
    assert data["sha256"] != "stale"
    loaded = load_adapter_cache(path)
    assert loaded.engine_name == "renamed-engine"
    assert loaded.runtime_bounds["max_memory_mb"] == 1024


def test_legacy_partial_checksum_requires_explicit_trusted_migration(tmp_path, adapter_engine_adapter_integrity):
    data = asdict(adapter_engine_adapter_integrity)
    legacy_payload = {"spec": data["source_spec"], "features": data["evaluation_features"]}
    data["sha256"] = hashlib.sha256(json.dumps(
        legacy_payload, sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Adapter checksum mismatch"):
        load_adapter_cache(path)
    legacy = load_adapter_cache(path, verify_checksum=False)
    assert asdict(legacy) == _read(path)
    migrated = export_adapter_cache(legacy, tmp_path / "migrated.json")
    loaded = load_adapter_cache(migrated)
    assert loaded.sha256 != data["sha256"]
    assert {k: v for k, v in asdict(loaded).items() if k != "sha256"} == {
        k: v for k, v in _read(path).items() if k != "sha256"
    }


def test_missing_file_still_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Adapter cache file not found"):
        load_adapter_cache(tmp_path / "missing.json")


# ═══ · fixture selection ═════════════════════════════════════════════════════════════════════════════════
# Offline selection is shared by both replay CLIs and lists without reading.

def test_filename_stem_ors_exclusions_and_symlinks(tmp_path):
    for name in ('first.json', 'second.json', 'other.txt'):
        (tmp_path / name).write_text('not JSON')
    assert [p.name for p in fixture_paths(tmp_path, patterns=('first', '*.json', 'first*'), exclude=('second',))] == ['first.json']
    with pytest.raises(FileNotFoundError):
        fixture_paths(tmp_path, patterns=('FIRST',))
    (tmp_path / 'directory.json').mkdir()
    try:
        (tmp_path / 'link.json').symlink_to(tmp_path / 'first.json')
    except (OSError, NotImplementedError):
        pass
    assert [p.name for p in fixture_paths(tmp_path)] == ['first.json', 'second.json']


@pytest.mark.parametrize('module', [jev_shim, prompt_experiment])
def test_list_does_not_read_replay_or_touch_output(tmp_path, monkeypatch, capsys, module):
    (tmp_path / 'selected.json').write_text('invalid JSON')
    def forbidden(*args, **kwargs):
        raise AssertionError('list must not read or replay')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(module, 'load_case', forbidden)
    monkeypatch.setattr(module, 'replay_case', forbidden)
    args = ['replay', '--fixtures', str(tmp_path), '--case', 'selected', '--list']
    assert module.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'cases': ['selected.json']}
    output = tmp_path / 'out.jsonl'
    output.write_bytes(b'keep existing audit')
    assert module.main(args + ['--out', str(output)]) == 2
    assert output.read_bytes() == b'keep existing audit'


@pytest.mark.parametrize('module', [jev_shim, prompt_experiment])
def test_replay_selection_preserves_existing_case_results_and_errors(tmp_path, capsys, module):
    all_cases = module.replay_fixtures()
    first = sorted(module.DEFAULT_FIXTURE_DIR.glob('*.json'))[0]
    selected = module.replay_fixtures(patterns=(first.stem,))
    assert len(selected) == 1 and selected[0] in all_cases
    (tmp_path / 'broken.json').write_text('invalid JSON')
    for options in (['--case', 'missing'], ['--case', ''], []):
        assert module.main(['replay', '--fixtures', str(tmp_path)] + options) == 2
        assert capsys.readouterr().out == ''


@pytest.mark.parametrize('module', ['jev_shim', 'prompt_experiment'])
def test_standalone_list_without_site_packages(tmp_path, module):
    (tmp_path / 'sample.json').write_text('not JSON')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    result = subprocess.run([sys.executable, '-S', '-m', 'mcp_toolcall_lab.' + module, 'replay',
                             '--fixtures', str(tmp_path), '--case', 'sample', '--list'],
                            env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['cases'] == ['sample.json']


@pytest.mark.parametrize('options,error', [({'patterns': '*'}, TypeError), ({'patterns': ('',)}, ValueError),
                                         ({'exclude': (1,)}, ValueError)])
def test_invalid_rules_are_rejected_on_empty_root(tmp_path, options, error):
    with pytest.raises(error):
        fixture_paths(tmp_path, **options)
