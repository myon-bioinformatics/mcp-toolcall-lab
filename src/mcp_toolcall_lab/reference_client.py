"""Shared MCP discovery/call layer for the reference/diagnostic UIs.

Gradio and Streamlit are *reference / diagnostic* clients in this lab (see the
README's "Client roles" section), not chat products under test the way Open
WebUI and LibreChat are. Both adapters must import this module rather than
each growing their own MCP client: if Gradio and Streamlit ever disagreed on
how a tool call is discovered, called, or classified, that disagreement would
only ever be a fact about this shared layer, never a fact about one UI
framework vs the other -- which is exactly what makes "did Gradio succeed
where the product client failed" a useful signal for narrowing a failure to
product-specific vs MCP/server-wide.

``ToolCallResult`` intentionally carries a few more fields than the current
Gradio/Streamlit adapters read (``prompt``, ``assistant_received_result``) so
that a later PR's case-record / anti-pattern correlation (see
docs/e2e_foundation.md) has somewhere to read them from without a rewrite of
this module. It does not itself write anywhere -- ``record.py``'s
MCP_TOOLCALL_LOG, keyed by the ``case_id``/``client`` tagged onto the
request's MCP ``_meta``, remains the one log both this module's callers and
the server middleware agree on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

OUTCOME_SUCCESS = "success"
OUTCOME_EMPTY = "empty"
OUTCOME_ERROR = "error"


def new_case_id() -> str:
    """A per-call id a later case record can key on (see docs/e2e_foundation.md)."""
    return f"case_{uuid.uuid4().hex[:24]}"


@dataclass
class ToolSpec:
    """One tool as advertised by ``tools/list``, independent of any UI framework."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCallResult:
    """Normalized outcome of one reference-client tool call.

    ``outcome`` reuses this repo's existing success/empty/error semantics
    (``record.py``) rather than inventing a fourth state, so a case record can
    stay compatible with data already logged by direct/chat-simulated calls.
    """

    case_id: str
    client: str
    prompt: str | None
    tool_name: str
    arguments: dict[str, Any]
    outcome: str
    payload: Any
    error: str | None = None
    assistant_received_result: bool = False


def _result_payload(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if not content:
        return []
    texts = [getattr(item, "text", None) for item in content]
    return texts[0] if len(texts) == 1 else texts


async def discover_tools(mcp_url: str) -> list[ToolSpec]:
    """List the tools this mock currently advertises, over a fresh MCP session."""
    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
    return [
        ToolSpec(name=tool.name, description=tool.description or "", input_schema=tool.inputSchema or {})
        for tool in listed.tools
    ]


async def call_tool(
    mcp_url: str,
    *,
    client: str,
    tool_name: str,
    arguments: dict[str, Any],
    prompt: str | None = None,
    case_id: str | None = None,
) -> ToolCallResult:
    """Call one tool and classify the result the same way for every reference UI.

    Tags the request's MCP ``_meta`` with ``case_id``/``client``/``source:
    "reference"`` -- the same correlation mechanism ``chat_sim.py`` uses for
    ``source: "chat"``/``"direct"`` -- so MCP_TOOLCALL_LOG rows from a
    Gradio or Streamlit call are distinguishable from both of those paths.
    """
    case_id = case_id or new_case_id()
    meta = {"case_id": case_id, "client": client, "source": "reference"}

    try:
        async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments, meta=meta)
    except Exception as exc:  # transport failure, timeout, etc. -- still a classifiable outcome
        return ToolCallResult(
            case_id=case_id,
            client=client,
            prompt=prompt,
            tool_name=tool_name,
            arguments=arguments,
            outcome=OUTCOME_ERROR,
            payload=None,
            error=str(exc),
        )

    payload = _result_payload(result)
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    if is_error:
        outcome = OUTCOME_ERROR
    elif payload in ([], {}):
        outcome = OUTCOME_EMPTY
    else:
        outcome = OUTCOME_SUCCESS

    return ToolCallResult(
        case_id=case_id,
        client=client,
        prompt=prompt,
        tool_name=tool_name,
        arguments=arguments,
        outcome=outcome,
        payload=payload,
        error=str(payload) if is_error else None,
        assistant_received_result=outcome != OUTCOME_ERROR,
    )
