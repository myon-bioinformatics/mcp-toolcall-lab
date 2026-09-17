"""Copy this entire file as a standalone mock MCP server for Open WebUI tests.

Install once with: pip install "fastmcp>=2,<3"
Run with:         python openwebui_mcp_mock.py

No API key or project-local imports are required. The default endpoint is
http://127.0.0.1:8000/mcp, using Streamable HTTP.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("mcp-toolcall-lab")
_LOG_PATH = os.environ.get("MCP_TOOLCALL_LOG")


def _record(tool_name: str, arguments: dict[str, object], result: object) -> None:
    """Optionally append a JSONL call record when MCP_TOOLCALL_LOG is set."""
    if not _LOG_PATH:
        return
    event = {
        "at": datetime.now(UTC).isoformat(),
        "tool": tool_name,
        "arguments": arguments,
        "result": result,
    }
    with Path(_LOG_PATH).open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")


@mcp.tool()
def find_municipalities(query: str) -> list[dict[str, str]]:
    """Find mock municipalities by name or prefecture."""
    normalized = query.strip().lower()
    rows = [
        {"code": "13101", "name": "Chiyoda", "prefecture": "Tokyo"},
        {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"},
        {"code": "12207", "name": "Matsudo", "prefecture": "Chiba"},
    ]
    result = [row for row in rows if normalized in row["name"].lower() or normalized in row["prefecture"].lower()]
    _record("find_municipalities", {"query": query}, result)
    return result


@mcp.tool()
def find_transaction_prices(municipality_code: str, year: int) -> list[dict[str, int | str]]:
    """Return a deterministic mock property transaction-price record."""
    result: list[dict[str, int | str]] = [
        {
            "municipality_code": municipality_code,
            "year": year,
            "price_yen": 52_000_000,
            "property_type": "condominium",
            "source": "mock",
        }
    ]
    _record("find_transaction_prices", {"municipality_code": municipality_code, "year": year}, result)
    return result


@mcp.tool()
def find_stations(municipality_code: str) -> list[dict[str, str]]:
    """Find mock stations in a municipality."""
    samples = {
        "14109": [{"name": "Yokohama", "line": "JR"}],
        "12207": [{"name": "Matsudo", "line": "JR Joban"}],
        "13101": [{"name": "Tokyo", "line": "JR"}],
    }
    result = samples.get(municipality_code, [])
    _record("find_stations", {"municipality_code": municipality_code}, result)
    return result


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8000")),
    )
