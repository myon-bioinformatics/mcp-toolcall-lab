"""Stdlib bits both lab mocks can import without taking on each other's protocol.

Used by the MCP server (via ``record``) and the OpenAI-compatible demo.
No FastMCP, no OpenAI ``tool_calls`` decision, no MCP ``_meta`` schema —
those stay in the mock that owns that wire.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

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
    """Lab conversation pin. Not LibreChat's conversationId or OWUI chat.id."""
    return f"chat_{uuid.uuid4().hex[:24]}"


def new_call_id() -> str:
    """OpenAI ``tool_calls[].id`` shape. Same mint for MCP debug and the OpenAI mock."""
    return f"call_{uuid.uuid4().hex[:24]}"


def id_from_headers(headers: Any, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty value among ``keys`` (case-insensitive)."""
    if not headers:
        return None
    items = headers.items() if hasattr(headers, "items") else []
    lowered = {str(key).lower(): str(value).strip() for key, value in items}
    for key in keys:
        value = lowered.get(key)
        if value:
            return value
    return None


def chat_id_from_headers(headers: Any) -> str | None:
    return id_from_headers(headers, CHAT_ID_HEADER_KEYS)


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL file; skip blank or broken lines (debug logs are append-only)."""
    target = Path(path)
    if not target.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def close_http11_sse(handler: Any) -> None:
    """End an HTTP/1.1 SSE response. Keep-alive with no length hangs the next request.

    Same class of bug as markdown ``demos/openai_compat_mock.py`` (c0dcd15).
    JSON responses that already send ``Content-Length`` do not need this.
    """
    handler.send_header("Connection", "close")
    handler.close_connection = True
