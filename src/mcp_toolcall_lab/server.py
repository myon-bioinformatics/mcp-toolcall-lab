"""FastMCP server exposing a small, deterministic real-estate-style mock API."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .catalog import TOOL_DESCRIPTIONS, search_municipalities, search_stations, search_transaction_prices
from .record import OUTCOME_EMPTY, OUTCOME_ERROR, OUTCOME_SUCCESS, record_call


def _tool_delay_seconds() -> float:
    raw = os.environ.get("MCP_TOOL_DELAY_SECONDS", "0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


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


class ObservabilityMiddleware(Middleware):
    """Log every tools/call, including unknown names and validation failures."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        delay = _tool_delay_seconds()
        if delay:
            await asyncio.sleep(delay)
        name = context.message.name
        arguments = dict(context.message.arguments or {})
        try:
            result = await call_next(context)
        except Exception as exc:
            record_call(tool=name, arguments=arguments, outcome=OUTCOME_ERROR, error=str(exc))
            raise
        payload = _result_payload(result)
        outcome = _outcome_for_result(result, payload)
        if outcome == OUTCOME_ERROR:
            record_call(tool=name, arguments=arguments, outcome=outcome, error=str(payload))
        else:
            record_call(tool=name, arguments=arguments, outcome=outcome, result=payload)
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
