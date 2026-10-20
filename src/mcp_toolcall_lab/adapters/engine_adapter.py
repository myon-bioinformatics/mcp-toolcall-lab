"""Upstream Distillation Engine Adapter for lightweight execution and CI reuse.

Supports Hugging Face Hub specs, Docker image specs, and local evaluation models
(such as Shogi AI evaluation functions / NNUE / GGUF CPU models).

In 1-pass execution, heavy upstream metadata and model observation data are distilled
into a lightweight DistilledAdapter artifact with SHA-256 checksums and provenance.
Subsequent executions or CI steps load the cached adapter for fast, low-cost execution
without GPU requirements or full upstream initialization.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from .source_adapter import provenance

SCHEMA_VERSION = "1.0"
SUPPORTED_SOURCES = ("huggingface", "docker", "local")


@dataclass(frozen=True)
class EngineSpec:
    engine_name: str
    source_type: str
    repo_or_image: str
    revision_or_tag: str
    files: tuple[str, ...]
    parameter_count: int | None
    device_requirement: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DistilledAdapter:
    adapter_id: str
    engine_name: str
    schema_version: str
    source_spec: dict[str, Any]
    evaluation_features: dict[str, Any]
    runtime_bounds: dict[str, Any]
    sha256: str
    provenance: dict[str, Any]
    created_at: str


def extract_engine_spec(
    *,
    engine_name: str,
    source_type: str = "huggingface",
    repo_or_image: str,
    revision_or_tag: str = "main",
    files: Sequence[str] = (),
    parameter_count: int | None = None,
    device_requirement: str = "cpu",
    metadata: dict[str, Any] | None = None,
) -> EngineSpec:
    """Validate and extract a provider-neutral engine/model specification."""
    name = engine_name.strip()
    if not name:
        raise ValueError("engine_name must be non-empty")

    stype = source_type.strip().lower()
    if stype not in SUPPORTED_SOURCES:
        raise ValueError(f"source_type must be one of {SUPPORTED_SOURCES}, got {source_type!r}")

    target = repo_or_image.strip()
    if not target:
        raise ValueError("repo_or_image must be non-empty")

    rev = revision_or_tag.strip()
    if not rev:
        raise ValueError("revision_or_tag must be non-empty")

    dev = device_requirement.strip().lower()
    if dev not in ("cpu", "gpu"):
        raise ValueError("device_requirement must be 'cpu' or 'gpu'")

    if parameter_count is not None and (not isinstance(parameter_count, int) or parameter_count < 0):
        raise ValueError("parameter_count must be a non-negative integer if provided")

    return EngineSpec(
        engine_name=name,
        source_type=stype,
        repo_or_image=target,
        revision_or_tag=rev,
        files=tuple(str(f).strip() for f in files if str(f).strip()),
        parameter_count=parameter_count,
        device_requirement=dev,
        metadata=dict(metadata or {}),
    )


def _compute_checksum(payload: dict[str, Any]) -> str:
    """Hash the complete cache except its top-level checksum field.

    This detects changes against the stored digest; it does not authenticate the
    producer when an attacker can replace both the payload and its digest.
    """
    serialized = json.dumps(
        {key: value for key, value in payload.items() if key != "sha256"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def distill_engine_adapter(
    spec: EngineSpec,
    observation_data: dict[str, Any],
    *,
    adapter_id: str | None = None,
    runtime_bounds: dict[str, Any] | None = None,
) -> DistilledAdapter:
    """Distill 1-pass execution/observation data into a lightweight adapter artifact."""
    if not isinstance(observation_data, dict):
        raise ValueError("observation_data must be a dict")

    aid = adapter_id.strip() if adapter_id and adapter_id.strip() else f"adapter-{spec.engine_name}-{spec.revision_or_tag}"

    bounds = {
        "max_cpu_cores": 4,
        "max_memory_mb": 2048,
        "max_execution_ms": 1000.0,
        **(runtime_bounds or {}),
    }

    spec_dict = asdict(spec)

    if spec.source_type == "huggingface":
        source_url = f"https://huggingface.co/{spec.repo_or_image}/tree/{spec.revision_or_tag}"
        api_url = f"https://huggingface.co/api/models/{spec.repo_or_image}"
    elif spec.source_type == "docker":
        source_url = f"docker://{spec.repo_or_image}:{spec.revision_or_tag}"
        api_url = None
    else:
        source_url = f"file://{spec.repo_or_image}"
        api_url = None

    prov = provenance(
        {"html_url": source_url},
        api_url=api_url,
        source_url=source_url,
    )

    eval_features = dict(observation_data)
    adapter = DistilledAdapter(
        adapter_id=aid,
        engine_name=spec.engine_name,
        schema_version=SCHEMA_VERSION,
        source_spec=spec_dict,
        evaluation_features=eval_features,
        runtime_bounds=bounds,
        sha256="",
        provenance=prov,
        created_at=datetime.now(UTC).isoformat(),
    )
    return replace(adapter, sha256=_compute_checksum(asdict(adapter)))


def export_adapter_cache(adapter: DistilledAdapter, file_path: Path | str) -> Path:
    """Export the distilled adapter to a JSON file for downstream CI / next execution reuse."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(adapter)
    # Nested dictionaries remain mutable even on a frozen dataclass. Hash the
    # snapshot actually written, including any changes since distillation/load.
    payload["sha256"] = _compute_checksum(payload)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_adapter_cache(file_path: Path | str, *, verify_checksum: bool = True) -> DistilledAdapter:
    """Load a cache; SHA-256 checks integrity, not producer authenticity.

    Legacy partial-checksum caches fail verification. Only explicitly trusted
    legacy inputs may be migrated using verify_checksum=False and re-exported.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Adapter cache file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version {data.get('schema_version')!r}, expected {SCHEMA_VERSION!r}")

    if verify_checksum:
        expected_checksum = data.get("sha256")
        actual_checksum = _compute_checksum(data)
        if expected_checksum != actual_checksum:
            raise ValueError(
                f"Adapter checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
            )

    return DistilledAdapter(
        adapter_id=data["adapter_id"],
        engine_name=data["engine_name"],
        schema_version=data["schema_version"],
        source_spec=data["source_spec"],
        evaluation_features=data["evaluation_features"],
        runtime_bounds=data["runtime_bounds"],
        sha256=data["sha256"],
        provenance=data["provenance"],
        created_at=data["created_at"],
    )


def execute_via_adapter(
    adapter: DistilledAdapter,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute lightweight evaluation using pre-distilled adapter features without upstream calls."""
    started = time.monotonic()

    features = adapter.evaluation_features
    # Simple lightweight feature matching / evaluation computation
    key = input_data.get("query") or input_data.get("position") or "default"

    evaluation = features.get(str(key)) or features.get("default") or {
        "evaluation_score": 0.0,
        "confidence": 0.5,
        "note": "fallback evaluation from distilled features",
    }

    elapsed_ms = round((time.monotonic() - started) * 1000, 3)

    return {
        "status": "ok",
        "adapter_id": adapter.adapter_id,
        "engine_name": adapter.engine_name,
        "evaluation": evaluation,
        "execution_ms": elapsed_ms,
        "upstream_bypassed": True,
        "device_used": adapter.source_spec.get("device_requirement", "cpu"),
    }
