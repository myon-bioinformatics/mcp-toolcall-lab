"""Simulate a generic chat UI tool-calling turn, wired to a real MCP call."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .correlation import normalize_correlation


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"


def new_chat_id() -> str:
    return f"chat_{uuid.uuid4().hex[:24]}"


@dataclass
class ChatTrace:
    chat_id: str
    call_id: str
    trace_id: str
    request_id: str
    user_message: dict[str, Any]
    assistant_tool_call_message: dict[str, Any]
    tool_result_message: dict[str, Any]
    mcp_result: Any


async def send_via_chat(
    mcp_url: str,
    *,
    user_text: str,
    tool_name: str,
    arguments: dict[str, Any],
    chat_id: str | None = None,
    call_id: str | None = None,
    correlation: dict[str, Any] | None = None,
) -> ChatTrace:
    """Run one simulated chat turn with a natural correlation envelope."""
    chat_id = chat_id or new_chat_id()
    call_id = call_id or new_call_id()
    meta = normalize_correlation(
        {**(correlation or {}), "chat_id": chat_id, "call_id": call_id},
        source="chat",
    )

    user_message = {"role": "user", "content": user_text}
    assistant_tool_call_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": arguments},
            }
        ],
    }

    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments, meta=meta)

    content_text = result.content[0].text if result.content else "[]"
    tool_result_message = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content_text,
    }

    return ChatTrace(
        chat_id=chat_id,
        call_id=call_id,
        trace_id=str(meta["trace_id"]),
        request_id=str(meta["request_id"]),
        user_message=user_message,
        assistant_tool_call_message=assistant_tool_call_message,
        tool_result_message=tool_result_message,
        mcp_result=result,
    )


async def send_direct(
    mcp_url: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    trace_id: str | None = None,
    correlation: dict[str, Any] | None = None,
) -> Any:
    raw = dict(correlation or {})
    if trace_id is not None:
        raw["trace_id"] = trace_id
    meta = normalize_correlation(raw, source="direct")
    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments, meta=meta)
