"""Tests for Upstream Distillation Engine Adapter (src/mcp_toolcall_lab/adapters/engine_adapter.py)."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from mcp_toolcall_lab.adapters.engine_adapter import (
    DistilledAdapter,
    EngineSpec,
    distill_engine_adapter,
    execute_via_adapter,
    export_adapter_cache,
    extract_engine_spec,
    load_adapter_cache,
)


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
    assert res1["device_used"] == "cpu"

    # Execute with fallback key
    res2 = execute_via_adapter(adapter, {"position": "unknown_pos"})
    assert res2["status"] == "ok"
    assert res2["evaluation"]["best_move"] == "none"
