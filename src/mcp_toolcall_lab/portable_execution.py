"""Portable JSON host: existing Python adapter + shared browser/Node trace core.

No engine download or inference. Requests never choose executables or filesystem
paths; the caller supplies a local adapter path out of band. See portable-execution.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .adapters.engine_adapter import execute_via_adapter, load_adapter_cache
from .mock.common import append_jsonl

CONTRACT = "lab-execution/1"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
CORE = Path(__file__).with_name("static") / "portable_cli.mjs"


def failure(code: str, message: str, status: str = "unsupported") -> dict[str, Any]:
    return {"schema": CONTRACT, "status": status, "execution_mode": "unsupported", "model_inference_performed": False, "metrics": None, "confidence": None, "error": {"code": code, "message": message}}


def canonical_request(request: dict[str, Any]) -> str:
    return json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_request(request).encode("utf-8")).hexdigest()


def execute(request: Any, *, adapter_path: str | Path | None = None, summary_dir: str | Path | None = None, native_only: bool = False) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != {"schema", "operation", "input"} or request.get("schema") != CONTRACT:
        return failure("invalid_input", "Expected schema, operation and input only, with schema lab-execution/1.")
    try:
        encoded = canonical_request(request).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return failure("invalid_input", "Request must be finite JSON with valid Unicode.")
    if len(encoded) > MAX_REQUEST_BYTES:
        return failure("invalid_input", "Request exceeds 2 MiB.")
    operation = request["operation"]
    if summary_dir is not None and (operation != "source_selection" or adapter_path is not None):
        return failure("invalid_input", "--summary-dir requires only source_selection, without --adapter.")
    if operation in ("trace_summary", "source_selection"):
        if native_only:
            return failure("runtime_unavailable", "Python native-only mode does not duplicate the JavaScript analyzers.")
        try:
            command = [os.environ.get("NODE", "node"), str(CORE), "--native-only"]
            if summary_dir is not None:
                command.extend(["--summary-dir", str(summary_dir)])
            completed = subprocess.run(command, input=encoded, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
            if len(completed.stdout) > 4 * 1024 * 1024:
                return failure("bridge_failed", "Node receipt exceeds 4 MiB.")
            result = json.loads(completed.stdout)
            if not isinstance(result, dict) or result.get("schema") not in ({CONTRACT, "lab-summary-receipt/1"} if summary_dir is not None else {CONTRACT}):
                return failure("bridge_failed", "Invalid Node receipt.")
            return result
        except (OSError, subprocess.SubprocessError, ValueError):
            return failure("bridge_failed", "Node analysis process failed or exceeded its 15-second bridge limit.")
    if operation != "adapter_lookup":
        return failure("invalid_input", "Unknown operation; no execution was performed.")
    data = request["input"]
    if not isinstance(data, dict) or set(data) != {"selector"} or not isinstance(data["selector"], dict):
        return failure("invalid_input", "adapter_lookup requires input.selector.")
    selector = data["selector"]
    if len(selector) > 1 or any(key not in {"query", "position"} or not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096 for key, value in selector.items()):
        return failure("invalid_input", "Use one nonempty query/position string (max 4096 bytes), or {} for the stored default.")
    if adapter_path is None:
        return failure("adapter_required", "Select a verified local cache with --adapter.")
    try:
        adapter = load_adapter_cache(adapter_path)
        receipt = execute_via_adapter(adapter, selector)
    except (OSError, ValueError, TypeError):
        return failure("invalid_adapter", "Local adapter cache is missing, malformed, or failed integrity verification.")
    result = {
        "schema": CONTRACT, "status": receipt["status"], "execution_mode": receipt["execution_mode"],
        "model_inference_performed": False, "metrics": None, "confidence": None,
        "confidence_reason": "Stored observations are not a calibrated model probability.",
        "adapter_receipt": receipt,
        "provenance": {"request_sha256": request_sha256(request), "adapter_sha256": adapter.sha256, "adapter_id": adapter.adapter_id, "source": "verified_local_adapter", "verification": "Cache checksum verifies content consistency, not producer authenticity."},
    }
    if "error" in receipt:
        result["error"] = receipt["error"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--summary-dir", type=Path, help="Verify exact local static summary artifacts; no model or network call")
    parser.add_argument("--native-only", action="store_true")
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("too large")
        request = json.loads(raw.decode("utf-8"))
        result = execute(request, adapter_path=args.adapter, summary_dir=args.summary_dir, native_only=args.native_only)
    except (ValueError, UnicodeError, RecursionError):
        result = failure("invalid_input", "Expected one UTF-8 JSON request no larger than 2 MiB.")
    # Reuse the lab ledger, but do not copy raw user input into it.
    if log := os.environ.get("MCP_TOOLCALL_LOG"):
        append_jsonl(log, {"event": "portable/execution", "status": result["status"], "execution_mode": result["execution_mode"], "provenance": result.get("provenance")})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
