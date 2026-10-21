"""Whole-cache integrity, JSON normalization, and legacy API contracts."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

from mcp_toolcall_lab.adapters.engine_adapter import (
    distill_engine_adapter,
    execute_via_adapter,
    export_adapter_cache,
    extract_engine_spec,
    load_adapter_cache,
)


@pytest.fixture
def adapter():
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
def test_each_cache_field_tamper_is_rejected(tmp_path, adapter, field, value):
    path = export_adapter_cache(adapter, tmp_path / "cache.json")
    data = _read(path)
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Adapter checksum mismatch"):
        load_adapter_cache(path)  # Verification remains enabled by default.


@pytest.mark.parametrize("field", ["runtime_bounds", "provenance", "created_at"])
def test_removed_field_is_rejected(tmp_path, adapter, field):
    path = export_adapter_cache(adapter, tmp_path / "cache.json")
    data = _read(path)
    del data[field]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Adapter checksum mismatch"):
        load_adapter_cache(path)


@pytest.mark.parametrize("change", ["schema", "extra", "nested_digest", "array_order"])
def test_complete_raw_payload_is_protected(tmp_path, adapter, change):
    path = export_adapter_cache(adapter, tmp_path / "cache.json")
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


def test_digest_matches_whole_exported_payload(tmp_path, adapter):
    path = export_adapter_cache(adapter, tmp_path / "cache.json")
    data = _read(path)
    assert adapter.sha256 == data["sha256"] == _digest(data)
    assert data["schema_version"] == "1.0"
    assert set(data) == set(asdict(adapter))
    loaded = load_adapter_cache(str(path), verify_checksum=True)
    assert asdict(loaded) == data
    result = execute_via_adapter(loaded, {})
    assert result["adapter_id"] == adapter.adapter_id
    assert result["evaluation"]["best_move"] == "7g7f"


def test_formatting_and_recursive_key_order_do_not_change_digest(tmp_path, adapter):
    def reorder(value):
        if isinstance(value, dict):
            return {k: reorder(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reorder(v) for v in value]
        return value

    path = export_adapter_cache(adapter, tmp_path / "cache.json")
    data = _read(path)
    path.write_text(json.dumps(reorder(data), ensure_ascii=True, indent=4), encoding="utf-8")
    assert load_adapter_cache(path).sha256 == adapter.sha256


def test_export_recomputes_digest_for_current_snapshot(tmp_path, adapter):
    changed = replace(adapter, engine_name="renamed-engine", sha256="stale")
    changed.runtime_bounds["max_memory_mb"] = 1024
    path = export_adapter_cache(changed, tmp_path / "cache.json")
    data = _read(path)
    assert data["sha256"] == _digest(data)
    assert data["sha256"] != "stale"
    loaded = load_adapter_cache(path)
    assert loaded.engine_name == "renamed-engine"
    assert loaded.runtime_bounds["max_memory_mb"] == 1024


def test_legacy_partial_checksum_requires_explicit_trusted_migration(tmp_path, adapter):
    data = asdict(adapter)
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
