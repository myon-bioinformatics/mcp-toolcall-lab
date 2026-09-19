"""FastMCP server exposing a small, deterministic real-estate-style mock API."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .catalog import TOOL_DESCRIPTIONS, search_municipalities, search_stations, search_transaction_prices
from .record import OUTCOME_EMPTY, OUTCOME_ERROR, OUTCOME_SUCCESS, record_call

# Chat products that opt in to forwarding their own conversation id as an
# HTTP header when calling a tool server (e.g. Open WebUI's
# ENABLE_FORWARD_USER_INFO_HEADERS + FORWARD_SESSION_INFO_HEADER_CHAT_ID,
# both verified against its own source, backend/open_webui/env.py and
# utils/tools.py's build_tool_server_headers()). Reading it here means a
# *real* chat UI's chat_id shows up in MCP_TOOLCALL_LOG automatically --
# no client-side _meta plumbing needed, unlike chat_sim.py's simulated chat,
# which has to set _meta by hand because nothing else can tag a call it
# makes up itself.
FORWARDED_CHAT_HEADERS = {
    "x-openwebui-chat-id": "chat_id",
    "x-openwebui-message-id": "message_id",
}


def _tool_delay_seconds() -> float:
    raw = os.environ.get("MCP_TOOL_DELAY_SECONDS", "0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _forwarded_chat_headers() -> dict[str, str]:
    """Read whatever known chat-correlation headers are on the current
    Streamable HTTP request, if any. Never raises: no active request (e.g. a
    direct MCP call with no chat UI in front of it) just means an empty dict,
    the same as get_http_headers() itself guarantees.

    Note: get_http_headers()'s `include` isn't an allowlist -- it only spares
    those specific names from its own default exclusion list, so ordinary
    headers outside that default list (accept-encoding, user-agent, ...)
    still come back too. Look up only the names this function actually
    knows about, rather than assuming every returned key is one of them."""
    headers = get_http_headers(include=set(FORWARDED_CHAT_HEADERS))
    return {
        display_name: headers[header_name]
        for header_name, display_name in FORWARDED_CHAT_HEADERS.items()
        if header_name in headers
    }


def _result_payload(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if not content:
        return []
    texts = [getattr(item, "text", None) for item in content]
    return texts[0] if len(texts) == 1 else texts


def _outcome_for_result(result: Any, payload: Any) -> str:
    if getattr(result, "is_error", False):
        return OUTCOME_ERROR
    if payload == [] or payload == {}:
        return OUTCOME_EMPTY
    return OUTCOME_SUCCESS


def _request_meta(context: MiddlewareContext) -> dict[str, Any]:
    """Pull the caller-supplied MCP `_meta` object off a tools/call request.

    Any client can attach arbitrary correlation data here (a chat-simulated
    caller's `call_id`/`chat_id`, or a direct caller's own trace id) — MCP
    reserves `_meta` exactly for this, so no protocol extension is needed.

    Note: `context.message.meta` is *not* the original request's `_meta` —
    FastMCP's own tools/call dispatch (fastmcp==3.4.7) rebuilds
    CallToolRequestParams internally and overwrites `_meta` with its own
    version-pinning metadata before middleware ever sees it. The original,
    client-supplied `_meta` survives on the lower-level request context
    instead, so that is what we read here.
    """
    ctx = context.fastmcp_context
    request_context = ctx.request_context if ctx is not None else None
    raw_meta = getattr(request_context, "meta", None) if request_context is not None else None
    if raw_meta is None:
        result: dict[str, Any] = {}
    else:
        if hasattr(raw_meta, "model_dump"):
            raw_meta = raw_meta.model_dump(mode="json", exclude_none=True)
        result = {k: v for k, v in dict(raw_meta).items() if k != "progressToken"}

    # Nested, not merged into the top level: keeps "the caller told us this"
    # (_meta, set by hand -- chat_sim.py, or a direct caller's own trace id)
    # visually distinct from "the transport told us this" (a forwarded HTTP
    # header), since a real chat product's Playwright test sets neither of
    # the former but always has the latter once forwarding is turned on.
    forwarded = _forwarded_chat_headers()
    if forwarded:
        result["forwarded_headers"] = forwarded
    return result


class ObservabilityMiddleware(Middleware):
    """Log every tools/call, including unknown names and validation failures."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        delay = _tool_delay_seconds()
        name = context.message.name
        arguments = dict(context.message.arguments or {})
        meta = _request_meta(context)
        # Measured around the artificial delay too, not just call_next(): a
        # caller (a test, a real chat product's own timeout budget) waits
        # for the whole thing, and MCP_TOOL_DELAY_SECONDS exists specifically
        # to simulate a slow call, so a duration that excluded it would
        # under-report exactly the case that setting exists to test.
        started = time.monotonic()
        if delay:
            await asyncio.sleep(delay)
        try:
            result = await call_next(context)
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            record_call(
                tool=name, arguments=arguments, outcome=OUTCOME_ERROR, error=str(exc), meta=meta, duration_ms=duration_ms
            )
            raise
        duration_ms = (time.monotonic() - started) * 1000
        payload = _result_payload(result)
        outcome = _outcome_for_result(result, payload)
        if outcome == OUTCOME_ERROR:
            record_call(
                tool=name, arguments=arguments, outcome=outcome, error=str(payload), meta=meta, duration_ms=duration_ms
            )
        else:
            record_call(
                tool=name, arguments=arguments, outcome=outcome, result=payload, meta=meta, duration_ms=duration_ms
            )
        return result


def create_mcp() -> FastMCP:
    """Build the Streamable HTTP mock server used by tests and Open WebUI."""
    mcp = FastMCP("mcp-toolcall-lab")
    mcp.add_middleware(ObservabilityMiddleware())

    @mcp.tool(description=TOOL_DESCRIPTIONS["find_municipalities"])
    def find_municipalities(query: str) -> list[dict[str, str]]:
        return search_municipalities(query)

    @mcp.tool(description=TOOL_DESCRIPTIONS["find_transaction_prices"])
    def find_transaction_prices(municipality_code: str, year: int) -> list[dict[str, int | str]]:
        return search_transaction_prices(municipality_code, year)

    @mcp.tool(description=TOOL_DESCRIPTIONS["find_stations"])
    def find_stations(municipality_code: str) -> list[dict[str, str]]:
        return search_stations(municipality_code)

    return mcp


def run_server(mcp: FastMCP | None = None) -> None:
    server = mcp or create_mcp()
    server.run(
        transport="http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8000")),
        path="/mcp",
    )


mcp = create_mcp()


if __name__ == "__main__":
    run_server(mcp)
