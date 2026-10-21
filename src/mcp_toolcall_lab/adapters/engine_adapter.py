"""Upstream Distillation Engine Adapter for lightweight execution and CI reuse.

Supports Hugging Face Hub specs, Docker image specs, and local evaluation models
(such as Shogi AI evaluation functions / NNUE / GGUF CPU models / LoRA / 1-bit BitNet / Qualcomm sub-byte specs).

In 1-pass execution, heavy upstream metadata and model observation data are distilled
into a lightweight DistilledAdapter artifact with SHA-256 checksums and provenance.
Subsequent executions or CI steps load the cached adapter for fast, low-cost execution
without GPU requirements or full upstream initialization.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Sequence

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
    quantization_bits: int | None
    quantization_type: str | None
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
    approximation_level: str
    compression_ratio: float
    sha256: str
    provenance: dict[str, Any]
    created_at: str


@contextmanager
def simulate_cuda_environment(
    *,
    device_name: str = "NVIDIA RTX 4090 (Simulated)",
    compute_capability: str = "8.9",
    vram_mb: int = 24576,
    simulated_vram_saved_mb: int = 18000,
) -> Generator[dict[str, Any], None, None]:
    """Simulate a CUDA GPU runtime environment offline without hardware or CUDA drivers."""
    sim_info = {
        "cuda_available": True,
        "device_name": device_name,
        "compute_capability": compute_capability,
        "vram_total_mb": vram_mb,
        "simulated_vram_saved_mb": simulated_vram_saved_mb,
        "cuda_simulation_active": True,
    }
    yield sim_info


def resolve_execution_device(
    requested_device: str,
    *,
    cuda_available: bool = False,
    simulated_cuda: dict[str, Any] | None = None,
) -> str:
    """Resolve device requirement, automatically substituting CUDA with cpu-lightweight when unavailable."""
    req = requested_device.strip().lower()
    has_cuda = cuda_available or (simulated_cuda is not None and bool(simulated_cuda.get("cuda_available")))
    if req in ("cuda", "gpu"):
        return "cuda" if has_cuda else "cpu-lightweight"
    return "cpu-lightweight" if req in ("cpu", "cpu-lightweight") else "cpu"


def extract_engine_spec(
    *,
    engine_name: str,
    source_type: str = "huggingface",
    repo_or_image: str,
    revision_or_tag: str = "main",
    files: Sequence[str] = (),
    parameter_count: int | None = None,
    quantization_bits: int | None = None,
    quantization_type: str | None = None,
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
    if dev not in ("cpu", "gpu", "cuda", "cpu-lightweight"):
        raise ValueError("device_requirement must be 'cpu', 'gpu', 'cuda', or 'cpu-lightweight'")

    if parameter_count is not None and (not isinstance(parameter_count, int) or parameter_count < 0):
        raise ValueError("parameter_count must be a non-negative integer if provided")

    if quantization_bits is not None and (not isinstance(quantization_bits, int) or quantization_bits < 1 or quantization_bits > 32):
        raise ValueError("quantization_bits must be an integer between 1 and 32 if provided")

    qtype = quantization_type.strip() if quantization_type else None

    return EngineSpec(
        engine_name=name,
        source_type=stype,
        repo_or_image=target,
        revision_or_tag=rev,
        files=tuple(str(f).strip() for f in files if str(f).strip()),
        parameter_count=parameter_count,
        quantization_bits=quantization_bits,
        quantization_type=qtype,
        device_requirement=dev,
        metadata=dict(metadata or {}),
    )


def _compute_checksum(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def convert_to_1bit_weights_delta(
    float_weights: Sequence[float],
    *,
    threshold: float = 0.1,
) -> dict[str, Any]:
    """Quantize floating point weights/logits into 1-bit ternary values (-1, 0, +1)."""
    if not float_weights:
        return {"ternary_bits": [], "scale": 1.0, "zero_point": 0}

    abs_sum = sum(abs(w) for w in float_weights)
    scale = abs_sum / len(float_weights) if float_weights else 1.0
    if scale == 0.0:
        scale = 1.0

    ternary = []
    for w in float_weights:
        normalized = w / scale
        if normalized > threshold:
            ternary.append(1)
        elif normalized < -threshold:
            ternary.append(-1)
        else:
            ternary.append(0)

    return {
        "ternary_bits": ternary,
        "scale": round(scale, 6),
        "zero_point": 0,
        "quantization_type": "1bit-ternary-bitnet",
        "elements_count": len(ternary),
    }


def distill_engine_adapter(
    spec: EngineSpec,
    observation_data: dict[str, Any],
    *,
    adapter_id: str | None = None,
    approximation_level: str = "lilbit-compressed",
    compression_ratio: float = 0.25,
    runtime_bounds: dict[str, Any] | None = None,
) -> DistilledAdapter:
    """Distill 1-pass execution/observation data into a lightweight adapter artifact."""
    if not isinstance(observation_data, dict):
        raise ValueError("observation_data must be a dict")
    if compression_ratio <= 0.0 or compression_ratio > 1.0:
        raise ValueError("compression_ratio must be between 0.0 (exclusive) and 1.0 (inclusive)")

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
    checksum = _compute_checksum({"spec": spec_dict, "features": eval_features})

    return DistilledAdapter(
        adapter_id=aid,
        engine_name=spec.engine_name,
        schema_version=SCHEMA_VERSION,
        source_spec=spec_dict,
        evaluation_features=eval_features,
        runtime_bounds=bounds,
        approximation_level=approximation_level,
        compression_ratio=compression_ratio,
        sha256=checksum,
        provenance=prov,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def create_lora_adapter(
    base_spec: EngineSpec,
    *,
    lora_rank: int = 8,
    lora_alpha: float = 16.0,
    target_modules: Sequence[str] = ("q_proj", "v_proj"),
    weights_delta: dict[str, Any] | None = None,
    adapter_id: str | None = None,
) -> DistilledAdapter:
    """Create a lightweight LoRA-style adapter delta from a base engine spec."""
    if lora_rank < 1:
        raise ValueError("lora_rank must be a positive integer")
    if lora_alpha <= 0:
        raise ValueError("lora_alpha must be positive")

    obs = {
        "adapter_type": "lora",
        "lora_rank": lora_rank,
        "lora_alpha": lora_alpha,
        "target_modules": list(target_modules),
        "weights_delta": weights_delta or {"bias_delta": 0.0, "scale": lora_alpha / lora_rank},
    }

    aid = adapter_id or f"lora-{base_spec.engine_name}-r{lora_rank}"
    return distill_engine_adapter(base_spec, obs, adapter_id=aid, approximation_level="lora-delta", compression_ratio=0.05)


def create_1bit_distilled_adapter(
    base_spec: EngineSpec,
    observation_data: dict[str, Any],
    *,
    weights_delta: Sequence[float] | None = None,
    adapter_id: str | None = None,
) -> DistilledAdapter:
    """Create a 1-bit / ternary extreme-quantization adapter for CPU bitwise evaluation."""
    quantized_delta = convert_to_1bit_weights_delta(weights_delta or []) if weights_delta else None

    obs = {
        **observation_data,
        "quantization_architecture": "1bit-bitnet-qualcomm",
        "weights_delta_1bit": quantized_delta,
    }

    aid = adapter_id or f"1bit-{base_spec.engine_name}"
    return distill_engine_adapter(
        base_spec,
        obs,
        adapter_id=aid,
        approximation_level="1bit-extreme-quantization",
        compression_ratio=0.03125,
    )


def can_apply_adapter(adapter: DistilledAdapter, target_engine_spec: EngineSpec) -> bool:
    """Evaluate whether a distilled adapter can be transferred/reused for a target model spec."""
    source_name = adapter.source_spec.get("engine_name")
    if source_name == target_engine_spec.engine_name:
        return True

    # Cross-model compatibility checks (e.g. same repo/family or matching device/quantization capabilities)
    source_repo = adapter.source_spec.get("repo_or_image")
    if source_repo and source_repo == target_engine_spec.repo_or_image:
        return True

    return False


def export_adapter_cache(adapter: DistilledAdapter, file_path: Path | str) -> Path:
    """Export the distilled adapter to a JSON file for downstream CI / next execution reuse."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(adapter)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_adapter_cache(file_path: Path | str, *, verify_checksum: bool = True) -> DistilledAdapter:
    """Load a distilled adapter from JSON and optionally verify its SHA-256 integrity."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Adapter cache file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version {data.get('schema_version')!r}, expected {SCHEMA_VERSION!r}")

    if verify_checksum:
        expected_checksum = data.get("sha256")
        actual_checksum = _compute_checksum({
            "spec": data["source_spec"],
            "features": data["evaluation_features"],
        })
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
        approximation_level=data.get("approximation_level", "lilbit-compressed"),
        compression_ratio=float(data.get("compression_ratio", 0.25)),
        sha256=data["sha256"],
        provenance=data["provenance"],
        created_at=data["created_at"],
    )


def execute_via_adapter(
    adapter: DistilledAdapter,
    input_data: dict[str, Any],
    *,
    cuda_available: bool = False,
    simulated_cuda: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute lightweight evaluation using pre-distilled adapter features without upstream calls or CUDA dependencies."""
    started = time.monotonic()

    req_device = adapter.source_spec.get("device_requirement", "cpu")
    effective_device = resolve_execution_device(
        req_device, cuda_available=cuda_available, simulated_cuda=simulated_cuda
    )
    cuda_fallback = (req_device in ("cuda", "gpu")) and not cuda_available and (simulated_cuda is None)

    features = adapter.evaluation_features
    # Simple lightweight feature matching / evaluation computation
    key = input_data.get("query") or input_data.get("position") or "default"

    evaluation = features.get(str(key)) or features.get("default") or {
        "evaluation_score": 0.0,
        "confidence": 0.5,
        "note": "fallback evaluation from distilled features",
    }

    elapsed_ms = round((time.monotonic() - started) * 1000, 3)

    result = {
        "status": "ok",
        "adapter_id": adapter.adapter_id,
        "engine_name": adapter.engine_name,
        "evaluation": evaluation,
        "execution_ms": elapsed_ms,
        "upstream_bypassed": True,
        "device_requested": req_device,
        "device_used": effective_device,
        "cuda_fallback_applied": cuda_fallback,
        "bypassed_cuda_requirement": cuda_fallback,
        "approximation_level": adapter.approximation_level,
        "compression_ratio": adapter.compression_ratio,
    }

    if simulated_cuda:
        result["cuda_simulated"] = True
        result["simulated_cuda_device"] = simulated_cuda.get("device_name")
        result["simulated_vram_saved_mb"] = simulated_cuda.get("simulated_vram_saved_mb", 18000)

    return result
