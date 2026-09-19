"""Small stdlib-only helpers for correlation ids shared by every client path.

The server must never pretend it knows a product chat id when the frontend did
not provide one. Instead we preserve known ids and guarantee a lab-owned
trace_id/request_id so every event remains joinable.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any

KNOWN_ID_KEYS = (
    "chat_id",
    "conversation_id",
    "call_id",
    "trace_id",
    "request_id",
    "completion_id",
    "response_id",
)

ENV_ID_KEYS = {
    "chat_id": "MCP_CHAT_ID",
    "conversation_id": "MCP_CONVERSATION_ID",
    "trace_id": "MCP_TRACE_ID",
    "request_id": "MCP_REQUEST_ID",
}


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:24]}"


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:24]}"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_correlation(
    meta: Mapping[str, Any] | None = None,
    *,
    source: str | None = None,
    ensure_trace: bool = True,
    ensure_request: bool = True,
) -> dict[str, Any]:
    """Return a conservative correlation envelope.

    Precedence is explicit meta -> environment fallback -> generated lab ids.
    Product-specific ids are never fabricated. Only trace_id/request_id are
    generated when absent.
    """
    raw = dict(meta or {})
    out: dict[str, Any] = {}

    for key in KNOWN_ID_KEYS:
        value = _clean(raw.get(key))
        if value is not None:
            out[key] = value

    for key, env_name in ENV_ID_KEYS.items():
        if key not in out:
            value = _clean(os.environ.get(env_name))
            if value is not None:
                out[key] = value

    if source:
        out["source"] = source
    elif _clean(raw.get("source")):
        out["source"] = _clean(raw["source"])

    if ensure_trace and "trace_id" not in out:
        out["trace_id"] = new_trace_id()
    if ensure_request and "request_id" not in out:
        out["request_id"] = new_request_id()

    for key, value in raw.items():
        if key not in out and key not in {"progressToken"}:
            out[key] = value
    return out


def correlation_key(meta: Mapping[str, Any] | None) -> str:
    """Best available stable join key for one event."""
    normalized = normalize_correlation(meta, ensure_trace=False, ensure_request=False)
    for key in ("chat_id", "conversation_id", "trace_id", "request_id", "call_id"):
        value = normalized.get(key)
        if value:
            return str(value)
    return ""
