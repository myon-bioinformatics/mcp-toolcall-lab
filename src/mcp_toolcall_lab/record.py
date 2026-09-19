"""JSONL call log shared by the package server and the generated standalone file."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

OUTCOME_SUCCESS = "success"
OUTCOME_EMPTY = "empty"
OUTCOME_ERROR = "error"

EVENT_TOOLS_CALL = "tools/call"

# Real chat UIs rarely put lab chat_id in MCP `_meta`. They *can* forward a
# conversation id on the HTTP hop; we also mint one per MCP session so a
# tools/call is never an orphan row.
CHAT_ID_HEADER_KEYS = (
    "x-chat-id",
    "x-lab-chat-id",
    "x-conversation-id",
    "x-owui-chat-id",
    "x-openwebui-chat-id",  # OWUI ENABLE_FORWARD_USER_INFO_HEADERS
)

MESSAGE_ID_HEADER_KEYS = (
    "x-openwebui-message-id",
    "x-message-id",
)


def new_chat_id() -> str:
    """Lab conversation pin. INLINE so the standalone mock can mint one too."""
    return f"chat_{uuid.uuid4().hex[:24]}"


def new_call_id() -> str:
    """OpenAI ``tool_calls[].id`` shape. One mint, used by chat_sim and the stub."""
    return f"call_{uuid.uuid4().hex[:24]}"


def _id_from_headers(headers: dict[str, str] | None, keys: tuple[str, ...]) -> str | None:
    if not headers:
        return None
    lowered = {str(key).lower(): str(value).strip() for key, value in headers.items()}
    for key in keys:
        value = lowered.get(key)
        if value:
            return value
    return None


def chat_id_from_headers(headers: dict[str, str] | None) -> str | None:
    return _id_from_headers(headers, CHAT_ID_HEADER_KEYS)


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
    message_id = _id_from_headers(headers, MESSAGE_ID_HEADER_KEYS)
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
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file; skip blank or broken lines (debug logs are append-only)."""
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
