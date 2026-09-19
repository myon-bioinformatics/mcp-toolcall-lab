"""JSONL call log shared by the package server and the generated standalone file."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTCOME_SUCCESS = "success"
OUTCOME_EMPTY = "empty"
OUTCOME_ERROR = "error"


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
    """Append one call record when MCP_TOOLCALL_LOG is set.

    `arguments` are the values received on `tools/call` before pydantic coercion.
    `outcome` is success, empty (valid call, no rows), or error.

    `meta` is whatever the caller put in the request's MCP `_meta` field (e.g.
    a chat-simulated caller's `call_id`/`chat_id`, or a caller talking to MCP
    directly passing its own correlation id) plus, under `meta.forwarded_headers`,
    any known chat-correlation HTTP header the transport itself carried (e.g. a
    real chat product's own chat_id, forwarded with no client-side wiring --
    see server.py's `_forwarded_chat_headers`). Both are opaque to this
    function — logged verbatim when present — so a trace can be reconstructed
    from this JSONL file regardless of which path (chat-simulated, a real
    chat UI, or a direct call) made the call.

    `duration_ms` is wall-clock time for the whole call as the caller
    experienced it (including any artificial `MCP_TOOL_DELAY_SECONDS` delay),
    not just the tool function's own execution.
    """
    log_path = os.environ.get("MCP_TOOLCALL_LOG")
    if not log_path:
        return
    event: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(),
        "tool": tool,
        "arguments": arguments,
        "outcome": outcome,
    }
    if duration_ms is not None:
        event["duration_ms"] = round(duration_ms, 3)
    if meta:
        event["meta"] = meta
    if outcome == OUTCOME_ERROR:
        event["error"] = error or "unknown error"
    else:
        event["result"] = result
    with Path(log_path).open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
