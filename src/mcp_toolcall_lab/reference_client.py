"""Shared MCP discovery/calling layer for the Gradio and Streamlit reference clients.

Gradio and Streamlit are reference/diagnostic clients, not chat products under
test (see README's "Client roles") — a successful call through either one helps
tell whether a failure is product-specific (Open WebUI, LibreChat) or
MCP/server-wide. Neither UI grows its own tool-calling logic: this module is
the one place that talks to MCP for both, so `apps/gradio_app.py` and
`apps/streamlit_app.py` stay thin UI adapters over the same discover/call/
normalize functions.

Unlike ``chat_sim.py`` (which simulates a model deciding to call a tool),
there is no model here — a human picks the tool and arguments through the UI.
``prompt`` is kept as a free-text field anyway so a case can still be recorded
as "what the human typed" alongside "what was actually called", matching the
E2E observability foundation in issue #14 (``case_id``, ``client``, ``prompt``,
``tool_called``, ``tool_name``, ``arguments``, ``mcp_outcome``,
``assistant_received_result``, ``classification``).

Each call is tagged with ``source: "reference"`` in the request's MCP `_meta`
field, so it lands in ``MCP_TOOLCALL_LOG`` (see ``record.py``) alongside
``chat_sim``'s ``"chat"``/``"direct"`` calls, correlated the same way — one
JSONL schema, distinguished only by ``meta.source``/``meta.client``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool

from .record import OUTCOME_EMPTY, OUTCOME_ERROR, OUTCOME_SUCCESS

CLASSIFICATION_TRANSPORT_FAILURE = "transport_failure"


def new_case_id() -> str:
    """Match the ``case_``-prefixed shape the observability foundation expects."""
    return f"case_{uuid.uuid4().hex[:24]}"


@dataclass
class ToolCallCase:
    """One normalized reference-client MCP round trip.

    Field names mirror issue #14's observability foundation directly so a
    later correlation pass can join this against ``MCP_TOOLCALL_LOG`` by
    ``case_id`` without renaming anything.
    """

    case_id: str
    client: str  # "gradio" | "streamlit"
    prompt: str
    tool_called: bool
    tool_name: str | None
    arguments: dict[str, Any]
    mcp_outcome: str | None  # success | empty | error
    assistant_received_result: bool
    classification: str
    result_text: str | None = None


async def discover_tools(mcp_url: str) -> list[Tool]:
    """Run ``tools/list`` so a UI can populate a tool picker from the live server."""
    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            return listed.tools


def _outcome_for_result(result: Any) -> str:
    if result.isError:
        return OUTCOME_ERROR
    # Match test_streamable_http_protocol.py's defensive alias lookup: the mcp
    # SDK's CallToolResult may expose this as either camelCase or snake_case
    # depending on version.
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    payload = structured.get("result") if isinstance(structured, dict) and "result" in structured else result.content
    if payload == [] or payload == {}:
        return OUTCOME_EMPTY
    return OUTCOME_SUCCESS


async def call_tool_for_prompt(
    mcp_url: str,
    *,
    client: str,
    prompt: str,
    tool_name: str,
    arguments: dict[str, Any],
    case_id: str | None = None,
) -> ToolCallCase:
    """Call one MCP tool on a reference client's behalf and normalize the outcome.

    A transport failure (server unreachable, connection reset, etc.) is
    reported as its own case rather than raised, so a UI adapter always has a
    ``ToolCallCase`` to render regardless of what went wrong.
    """
    case_id = case_id or new_case_id()
    meta = {"case_id": case_id, "client": client, "source": "reference"}

    try:
        async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments, meta=meta)
    except Exception as exc:
        return ToolCallCase(
            case_id=case_id,
            client=client,
            prompt=prompt,
            tool_called=True,
            tool_name=tool_name,
            arguments=arguments,
            mcp_outcome=OUTCOME_ERROR,
            assistant_received_result=False,
            classification=CLASSIFICATION_TRANSPORT_FAILURE,
            result_text=str(exc),
        )

    outcome = _outcome_for_result(result)
    result_text = result.content[0].text if result.content else ""
    return ToolCallCase(
        case_id=case_id,
        client=client,
        prompt=prompt,
        tool_called=True,
        tool_name=tool_name,
        arguments=arguments,
        mcp_outcome=outcome,
        assistant_received_result=True,
        classification=outcome,
        result_text=result_text,
    )
