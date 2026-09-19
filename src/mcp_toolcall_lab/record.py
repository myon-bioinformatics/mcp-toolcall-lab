"""JSONL call log shared by the package server and the generated standalone file."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTCOME_SUCCESS = "success"
OUTCOME_EMPTY = "empty"
OUTCOME_ERROR = "error"
EVENT_SCHEMA_VERSION = 2


def _normalized_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize correlation without depending on another inline module."""
    raw = dict(meta or {})
    raw.pop("progressToken", None)
    raw.setdefault("trace_id", os.environ.get("MCP_TRACE_ID") or f"trace_{uuid.uuid4().hex[:24]}")
    raw.setdefault("request_id", os.environ.get("MCP_REQUEST_ID") or f"req_{uuid.uuid4().hex[:24]}")
    for key, env_name in (
        ("chat_id", "MCP_CHAT_ID"),
        ("conversation_id", "MCP_CONVERSATION_ID"),
    ):
        if not raw.get(key) and os.environ.get(env_name):
            raw[key] = os.environ[env_name]
    return raw


def record_call(
    *,
    tool: str,
    arguments: dict[str, Any],
    outcome: str,
    result: Any = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
    duration_ms: float | None = None,
) -> None:
    """Append one versioned call record when MCP_TOOLCALL_LOG is set.

    Product chat ids are preserved only when supplied by a frontend/caller.
    A lab-owned trace_id and request_id are always present so anonymous calls
    are still debuggable and joinable.
    """
    log_path = os.environ.get("MCP_TOOLCALL_LOG")
    if not log_path:
        return
    event: dict[str, Any] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": f"evt_{uuid.uuid4().hex[:24]}",
        "at": datetime.now(UTC).isoformat(),
        "tool": tool,
        "arguments": arguments,
        "outcome": outcome,
        "meta": _normalized_meta(meta),
    }
    if duration_ms is not None:
        event["duration_ms"] = round(max(0.0, duration_ms), 3)
    if outcome == OUTCOME_ERROR:
        event["error"] = error or "unknown error"
    else:
        event["result"] = result
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
