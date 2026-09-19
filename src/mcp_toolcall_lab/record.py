"""JSONL call log shared by the package server and the generated standalone file.

Id mints, header lookup, and the JSONL writer live in ``mock.common`` so the
OpenAI demo can use the same helpers without importing this MCP log schema.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Callable

from .mock.common import (
    CHAT_ID_HEADER_KEYS,
    MESSAGE_ID_HEADER_KEYS,
    append_jsonl,
    chat_id_from_headers,
    id_from_headers,
    new_call_id,
    new_chat_id,
    read_jsonl,
)

OUTCOME_SUCCESS = "success"
OUTCOME_EMPTY = "empty"
OUTCOME_ERROR = "error"

# MCP JSON-RPC method names written as ``event``. Not a lab-specific protocol.
EVENT_INITIALIZE = "initialize"
EVENT_INITIALIZED = "notifications/initialized"
EVENT_TOOLS_LIST = "tools/list"
EVENT_TOOLS_CALL = "tools/call"

__all__ = [
    "CHAT_ID_HEADER_KEYS",
    "EVENT_INITIALIZE",
    "EVENT_INITIALIZED",
    "EVENT_TOOLS_CALL",
    "EVENT_TOOLS_LIST",
    "MESSAGE_ID_HEADER_KEYS",
    "OUTCOME_EMPTY",
    "OUTCOME_ERROR",
    "OUTCOME_SUCCESS",
    "chat_id_from_headers",
    "id_from_headers",
    "mcp_tool_calls",
    "new_call_id",
    "new_chat_id",
    "read_jsonl",
    "record_call",
    "record_protocol_event",
    "resolve_correlation",
    "usable_session_id",
]


def usable_session_id(session_id: object | None) -> str | None:
    """Keep a missing MCP session missing. ``str(None)`` is ``"None"``, not an id."""
    if session_id is None:
        return None
    text = str(session_id).strip()
    if not text or text == "None":
        return None
    return text


def resolve_correlation(
    *,
    meta: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
    session_id: str | None = None,
    session_chats: dict[str, str] | None = None,
    mint: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Resolve a chat_id without rewriting the caller's `_meta`.

    Priority: ``meta.chat_id`` → well-known headers → this MCP session's
    previously minted id → mint a new ``chat_*`` and remember it for the session.
    """
    meta = dict(meta or {})
    minted = mint or new_chat_id
    session_id = usable_session_id(session_id)
    if meta.get("chat_id"):
        chat_id = str(meta["chat_id"])
        source = "meta"
    else:
        header_id = chat_id_from_headers(headers)
        if header_id:
            chat_id = header_id
            source = "header"
        elif session_id and session_chats is not None and session_id in session_chats:
            chat_id = session_chats[session_id]
            source = "session"
        else:
            chat_id = minted()
            source = "minted"

    if session_id and session_chats is not None:
        session_chats.setdefault(session_id, chat_id)

    debug: dict[str, Any] = {
        "chat_id": chat_id,
        "chat_id_source": source,
    }
    if session_id:
        debug["session_id"] = session_id
    message_id = id_from_headers(headers, MESSAGE_ID_HEADER_KEYS)
    if message_id:
        debug["message_id"] = message_id
    if meta.get("call_id") is not None:
        debug["call_id"] = meta["call_id"]
        debug["call_id_source"] = "meta"
    else:
        debug["call_id"] = new_call_id()
        debug["call_id_source"] = "minted"
    if meta.get("trace_id") is not None:
        debug["trace_id"] = meta["trace_id"]
    if meta.get("source") is not None:
        debug["source"] = meta["source"]
    return debug


def record_call(
    *,
    tool: str,
    arguments: dict[str, Any],
    outcome: str,
    result: Any = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
    duration_ms: float | None = None,
    event: str = EVENT_TOOLS_CALL,
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

    `debug` is server-side correlation (resolved chat_id, source, session,
    result_n). It is *not* merged into `meta`, so existing exact-meta tests
    and callers keep a clean wire object.
    """
    log_path = os.environ.get("MCP_TOOLCALL_LOG")
    if not log_path:
        return
    row: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(),
        "event": event,
        "tool": tool,
        "arguments": arguments,
        "outcome": outcome,
    }
    if duration_ms is not None:
        row["duration_ms"] = duration_ms
    if meta:
        row["meta"] = meta
    debug_row = dict(debug) if debug else {}
    if outcome == OUTCOME_ERROR:
        row["error"] = error or "unknown error"
    else:
        row["result"] = result
        if isinstance(result, list):
            debug_row["result_n"] = len(result)
    if debug_row:
        row["debug"] = debug_row
        if debug_row.get("chat_id"):
            row["chat_id"] = debug_row["chat_id"]
    append_jsonl(log_path, row)


def record_protocol_event(
    *,
    event: str,
    debug: dict[str, Any] | None = None,
) -> None:
    """Append one MCP JSON-RPC method row (initialize / tools/list / ...).

    Same JSONL as ``record_call``. ``event`` is the wire method name.
    """
    log_path = os.environ.get("MCP_TOOLCALL_LOG")
    if not log_path:
        return
    row: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(),
        "event": event,
    }
    debug_row = dict(debug) if debug else {}
    if debug_row:
        row["debug"] = debug_row
        if debug_row.get("chat_id"):
            row["chat_id"] = debug_row["chat_id"]
    append_jsonl(log_path, row)


def mcp_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``tools/call`` rows only. Handshake events are not tool executions."""
    return [
        event
        for event in events
        if event.get("event", EVENT_TOOLS_CALL) == EVENT_TOOLS_CALL and event.get("tool")
    ]
