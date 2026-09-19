"""Simulate a chat UI's tool-calling turn, wired to a real MCP call.

Open WebUI, LibreChat, LobeChat, and most other chat UIs that support
function/tool calling all speak the same OpenAI-compatible wire shape for it:
the assistant message carries ``tool_calls: [{"id": "call_xxx", "type":
"function", "function": {"name", "arguments"}}]``, and the tool's result comes
back as ``{"role": "tool", "tool_call_id": "call_xxx", "content": ...}``. That
shared shape — not any one UI's own internal identifiers — is what this
module mocks, so it stands in for "a chat UI" generically rather than
reimplementing one specific product.

The ``call_id``/``chat_id`` this module generates are *not* modeled on any
one UI's database schema. Verified against Open WebUI's actual SQLAlchemy
models (see ``docs/openwebui_schema_notes.md``): its ``chat.id`` and
``chat.share_id`` are real, generated-and-stored identifiers comparable in
spirit to this module's ``chat_id``, but its ``function.id``/``tool.id`` are
user-chosen slugs for its own Python plugin system — a different kind of "id"
entirely, unrelated to MCP tool names or this module's ``call_id``. A
dedicated Open WebUI-specific mock (its own database shape, not just the
tool-calling wire format) is deferred; this module intentionally stays
generic until that's scoped.

This is deliberately not part of ``INLINE_MODULES`` in export.py: it is a
test/harness-side stand-in for *a caller* of the mock MCP server, not part of
the server itself. Other minted ids (completion / reasoning / UI conversation)
are harvested by ``trace_probe.py``, not invented here.

No LLM is called — matching the rest of this repo, everything here is
deterministic and offline. ``tool_name``/``arguments`` stand in for "the model
decided to call this tool"; this module's only job is wrapping that decision
in the message shapes a real chat UI would produce and routing the actual
call through a real MCP session, so the "chat-simulated" and "direct to MCP"
delivery paths can be compared and traced through the same JSONL log (see
``record.py``'s ``meta`` parameter and ``server.py``'s ``_request_meta``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from mcp_toolcall_lab.record import new_call_id, new_chat_id

__all__ = [
    "ChatTrace",
    "new_call_id",
    "new_chat_id",
    "send_direct",
    "send_via_chat",
]


@dataclass
class ChatTrace:
    """Every message/step of one simulated chat-to-MCP round trip, in order."""

    chat_id: str
    call_id: str
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
    trace_id: str | None = None,
) -> ChatTrace:
    """Run one simulated chat turn that decides to call ``tool_name``.

    Builds the OpenAI-compatible ``user`` -> ``assistant`` (tool_calls) ->
    ``tool`` message sequence, and executes the middle step against a real
    MCP server over Streamable HTTP — the same client flow
    ``test_streamable_http_protocol.py`` uses — tagging the request's `_meta`
    with ``chat_id``/``call_id``/``source: "chat"`` so it is traceable in the
    MCP_TOOLCALL_LOG JSONL alongside calls that went straight to MCP.
    """
    chat_id = chat_id or new_chat_id()
    call_id = call_id or new_call_id()
    meta = {"call_id": call_id, "chat_id": chat_id, "source": "chat"}
    if trace_id is not None:
        meta["trace_id"] = trace_id

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
            result = await session.call_tool(
                tool_name,
                arguments,
                meta=meta,
            )

    content_text = result.content[0].text if result.content else "[]"
    tool_result_message = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content_text,
    }

    return ChatTrace(
        chat_id=chat_id,
        call_id=call_id,
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
) -> Any:
    """Call MCP straight, with no chat layer — the other "delivery path".

    Tags the request's `_meta` with ``source: "direct"`` (and an optional
    caller-supplied ``trace_id``) so the JSONL log can be correlated the same
    way as a chat-simulated call, even though nothing here resembles a chat
    message.
    """
    meta: dict[str, Any] = {"source": "direct"}
    if trace_id is not None:
        meta["trace_id"] = trace_id

    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments, meta=meta)
