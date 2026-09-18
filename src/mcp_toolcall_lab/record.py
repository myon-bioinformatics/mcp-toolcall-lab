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
) -> None:
    """Append one call record when MCP_TOOLCALL_LOG is set.

    `arguments` are the values received on `tools/call` before pydantic coercion.
    `outcome` is success, empty (valid call, no rows), or error.

    `meta` is whatever the caller put in the request's MCP `_meta` field (e.g.
    a chat-simulated caller's `call_id`/`chat_id`, or a caller talking to MCP
    directly passing its own correlation id). It is opaque to this function —
    logged verbatim when present — so a trace can be reconstructed from this
    JSONL file regardless of which path (chat-simulated or direct) made the
    call.
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
    if meta:
        event["meta"] = meta
    if outcome == OUTCOME_ERROR:
        event["error"] = error or "unknown error"
    else:
        event["result"] = result
    with Path(log_path).open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
