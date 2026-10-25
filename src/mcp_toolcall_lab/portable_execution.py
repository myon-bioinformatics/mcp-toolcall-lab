"""Portable JSON host: existing Python adapter + shared browser/Node trace core.

No engine download or inference. Requests never choose executables or filesystem
paths; the caller supplies a local adapter path out of band. See portable-execution.md.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from .adapters.engine_adapter import execute_via_adapter, load_adapter_cache
from .mock.common import append_jsonl

CONTRACT = "lab-execution/1"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
NODE_BRIDGE_TIMEOUT = 15
NODE_RECEIPT_MAX_BYTES = 4 * 1024 * 1024
CORE = Path(__file__).with_name("static") / "portable_cli.mjs"
_NODE_LAUNCH: Any = None


class ReceiptTooLargeError(Exception):
    """Node stdout exceeded NODE_RECEIPT_MAX_BYTES."""


def failure(code: str, message: str, status: str = "unsupported") -> dict[str, Any]:
    return {"schema": CONTRACT, "status": status, "execution_mode": "unsupported", "model_inference_performed": False, "metrics": None, "confidence": None, "error": {"code": code, "message": message}}


def canonical_request(request: dict[str, Any]) -> str:
    return json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_request(request).encode("utf-8")).hexdigest()


def _node_launch_module() -> Any:
    global _NODE_LAUNCH
    if _NODE_LAUNCH is None:
        path = Path(__file__).resolve().parents[2] / "vendor" / "node_launch.py"
        spec = importlib.util.spec_from_file_location("_vendored_node_launch", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _NODE_LAUNCH = module
    return _NODE_LAUNCH


def _raise_portable_node_not_found(exc: BaseException, node: str | os.PathLike[str] | None) -> None:
    message = str(exc)
    if "node executable not found: " in message:
        path = message.rsplit("node executable not found: ", 1)[1]
        if node is not None or os.environ.get("NODE") == path:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path) from exc
    version = re.search(r"must be 24 or newer \(found (\d+)\)", message)
    if version is not None:
        raise RuntimeError(f"Node 24+ required; found Node {version.group(1)}") from exc
    if node is None and "node executable not found" in message:
        discovered = shutil.which("node")
        if discovered:
            node_launch = _node_launch_module()
            try:
                node_launch._probe_node_once(discovered, node_launch.DEFAULT_PROBE_TIMEOUT)
            except node_launch.NodeNotFoundError as probe_exc:
                version = re.search(r"must be 24 or newer \(found (\d+)\)", str(probe_exc))
                if version is not None:
                    raise RuntimeError(
                        f"Node 24+ required; found Node {version.group(1)}"
                    ) from exc
    raise RuntimeError("Node 24+ required; node executable not found on PATH") from exc


def _map_node_launch_error(
    exc: Any,
    command: list[str],
    timeout: float | None,
) -> subprocess.CompletedProcess[bytes]:
    if exc.code == "ELAUNCHSTDINCLOSED":
        status = exc.status if exc.status is not None else -1
        return subprocess.CompletedProcess(command, status, exc.stdout, exc.stderr)
    if exc.code == "ELAUNCHFAILURE" and "timed out" in str(exc):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=timeout,
            output=exc.stdout,
            stderr=exc.stderr,
        ) from exc
    raise exc


def _portable_node_run(
    input: bytes,
    *,
    timeout: float = NODE_BRIDGE_TIMEOUT,
    node: str | os.PathLike[str] | None = None,
    check: bool = True,
    summary_dir: str | Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    node_launch = _node_launch_module()
    env_var = "NODE"
    try:
        executable = node_launch.resolve_node(node=node, env_var=env_var)
    except node_launch.NodeNotFoundError as exc:
        _raise_portable_node_not_found(exc, node)
    arguments = ["--native-only"]
    if summary_dir is not None:
        arguments.extend(["--summary-dir", str(summary_dir)])
    command = node_launch.node_command(
        CORE, *arguments, node=executable, probe=False, env_var=env_var
    )
    try:
        completed = node_launch.run(
            CORE,
            *arguments,
            input=input,
            timeout=timeout,
            response_max_bytes=NODE_RECEIPT_MAX_BYTES,
            node=executable,
            probe=False,
            env_var=env_var,
            check=check,
        )
    except node_launch.NodeNotFoundError as exc:
        _raise_portable_node_not_found(exc, node)
    except node_launch.LaunchError as exc:
        if exc.code == "ERESPONSETOOLARGE":
            raise ReceiptTooLargeError() from exc
        if check and exc.code == "ELAUNCHFAILURE" and exc.status and "timed out" not in str(exc):
            raise subprocess.CalledProcessError(exc.status, command, exc.stdout, exc.stderr) from exc
        completed = _map_node_launch_error(exc, command, timeout)
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode, completed.args, completed.stdout, completed.stderr
            )
        return completed
    return completed


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
            completed = _portable_node_run(encoded, check=True, summary_dir=summary_dir)
            result = json.loads(completed.stdout)
            if not isinstance(result, dict) or result.get("schema") not in ({CONTRACT, "lab-summary-receipt/1"} if summary_dir is not None else {CONTRACT}):
                return failure("bridge_failed", "Invalid Node receipt.")
            return result
        except ReceiptTooLargeError:
            return failure("bridge_failed", "Node receipt exceeds 4 MiB.")
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
            return failure("bridge_failed", "Node log-analysis process failed or exceeded its 15-second bridge limit.")
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
