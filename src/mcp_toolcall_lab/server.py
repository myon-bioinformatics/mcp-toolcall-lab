"""FastMCP server exposing a small, deterministic real-estate-style mock API."""

from __future__ import annotations

import os

from fastmcp import FastMCP

from .catalog import search_municipalities, search_stations, search_transaction_prices

mcp = FastMCP("mcp-toolcall-lab")


@mcp.tool()
def find_municipalities(query: str) -> list[dict[str, str]]:
    """Find mock municipalities by name or prefecture. Use tools/list before calling."""
    return search_municipalities(query)


@mcp.tool()
def find_transaction_prices(municipality_code: str, year: int) -> list[dict[str, int | str]]:
    """Return mock property transaction prices. This never calls a live API."""
    return search_transaction_prices(municipality_code, year)


@mcp.tool()
def find_stations(municipality_code: str) -> list[dict[str, str]]:
    """Find mock stations in a municipality."""
    return search_stations(municipality_code)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8000")),
    )
