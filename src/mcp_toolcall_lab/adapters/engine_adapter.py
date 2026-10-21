"""Replay caller-supplied observations with explicit cache/approximation receipts.

EngineSpec describes an upstream source; it does not initialize or run it.
The historical DistilledAdapter API stores observations, not distilled weights.
This module uses only the standard library and never performs model inference.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from .source_adapter import provenance

SCHEMA_VERSION = "1.0"
EXECUTION_CONTRACT_VERSION = "2.0"
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
    """Store caller-supplied observations; no upstream execution or weight distillation."""
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

    if not isinstance(data, dict):
        raise ValueError("Adapter cache must be a JSON object")

    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version {data.get('schema_version')!r}, expected {SCHEMA_VERSION!r}")

    if verify_checksum:
        expected_checksum = data.get("sha256")
        actual_checksum = _compute_checksum(data)
        if expected_checksum != actual_checksum:
            raise ValueError(
                f"Adapter checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
            )

    for field in ("source_spec", "evaluation_features", "runtime_bounds", "provenance"):
        if not isinstance(data.get(field), dict):
            raise ValueError(f"Adapter cache {field} must be an object")
    for field in ("adapter_id", "engine_name", "sha256", "created_at"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise ValueError(f"Adapter cache {field} must be a non-empty string")

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
    *,
    on_miss: str = "unsupported",
    max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Replay a keyed observation, or explicitly opt into a constant approximation.

    Result contract 2.0 keeps legacy result keys and cache schema 1.0. Unknown
    keys no longer silently succeed. ``on_miss="stored_default"`` opts into the
    historical default fallback, labeled approximation with unmeasured quality.
    ``max_age_seconds`` optionally expires the entire observation snapshot from
    its UTC/offset-aware created_at, including default approximations.

    Inputs are observation selectors, not arbitrary model requests: one nonempty
    string query/position, or {} to select the literal "default" observation.
    Additional fields and ambiguous selectors are unsupported; they must not be
    silently ignored as though they were part of an exact model-result cache.
    """
    if on_miss not in ("unsupported", "stored_default"):
        raise ValueError("on_miss must be 'unsupported' or 'stored_default'")
    if max_age_seconds is not None and (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or (isinstance(max_age_seconds, float) and not math.isfinite(max_age_seconds))
        or max_age_seconds < 0
    ):
        raise ValueError("max_age_seconds must be a finite non-negative number")

    started = time.monotonic()
    result: dict[str, Any] = {
        "result_contract_version": EXECUTION_CONTRACT_VERSION,
        "status": "unsupported",
        "adapter_id": adapter.adapter_id,
        "engine_name": adapter.engine_name,
        "evaluation": None,
        "execution_mode": "unsupported",
        "result_source": None,
        "cache_hit": False,
        "matched_key": None,
        "model_inference_performed": False,
        "upstream_bypassed": True,
        "device_used": "unknown",
        "device_requirement": adapter.source_spec.get("device_requirement", "unknown"),
        "quality": {"accuracy": None, "error_bound": None, "calibrated": False},
    }

    def finish(**fields: Any) -> dict[str, Any]:
        result.update(fields)
        result["execution_ms"] = round((time.monotonic() - started) * 1000, 3)
        return result

    if not isinstance(input_data, dict) or (
        input_data and (len(input_data) != 1 or not set(input_data) <= {"query", "position"})
    ):
        return finish(error={"code": "unsupported_input", "message": "Use one query or position selector, or {}."})
    key = next(iter(input_data.values())) if input_data else "default"
    if not isinstance(key, str) or not key:
        return finish(error={"code": "unsupported_input", "message": "Observation selectors must be non-empty strings."})

    if max_age_seconds is not None:
        try:
            created = datetime.fromisoformat(adapter.created_at)
            if created.tzinfo is None:
                raise ValueError("created_at must include a timezone")
            age = (datetime.now(UTC) - created).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return finish(error={"code": "invalid_cache_timestamp", "message": "Cannot determine snapshot age."})
        if age < 0:
            return finish(error={"code": "invalid_cache_timestamp", "message": "Snapshot creation time is in the future."})
        if age >= max_age_seconds:
            return finish(status="cache_miss", error={"code": "cache_expired", "message": "Observation snapshot expired."})

    features = adapter.evaluation_features
    if key in features:
        return finish(
            status="ok", evaluation=deepcopy(features[key]),
            execution_mode="cached_observation", result_source="evaluation_features",
            cache_hit=True, matched_key=key,
        )
    if on_miss == "stored_default" and "default" in features:
        return finish(
            status="ok", evaluation=deepcopy(features["default"]),
            execution_mode="approximation", result_source="evaluation_features.default",
            matched_key="default",
            approximation={
                "method": "stored_default_constant_v1",
                "limitations": [
                    "Returns the caller-supplied default unchanged, independent of the input.",
                    "No model execution, similarity search, or quality validation is performed.",
                    "Any stored confidence is caller-supplied, not a calibrated probability.",
                ],
            },
        )
    return finish(status="cache_miss", error={"code": "cache_miss", "message": "No observation for this selector; no model backend is executed."})
