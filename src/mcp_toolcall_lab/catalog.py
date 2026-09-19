"""Pure-Python mock data used by the FastMCP server and unit tests.

A valid call that matches nothing returns an empty list. That is not an error.
Protocol problems (unknown tool, invalid arguments, timeout) are errors.
"""

from __future__ import annotations

from typing import Any

AVAILABLE_TOOLS = (
    "find_municipalities",
    "find_transaction_prices",
    "find_stations",
)

TOOL_DESCRIPTIONS = {
    "find_municipalities": "Find mock municipalities by name or prefecture.",
    "find_transaction_prices": "Return mock property transaction prices. This never calls a live API.",
    "find_stations": "Find mock stations in a municipality.",
}

MUNICIPALITIES = (
    {"code": "13101", "name": "Chiyoda", "prefecture": "Tokyo"},
    {"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"},
    {"code": "12207", "name": "Matsudo", "prefecture": "Chiba"},
)

STATIONS = {
    "14109": [{"name": "Yokohama", "line": "JR"}],
    "12207": [{"name": "Matsudo", "line": "JR Joban"}],
    "13101": [{"name": "Tokyo", "line": "JR"}],
}

KNOWN_MUNICIPALITY_CODES = tuple(row["code"] for row in MUNICIPALITIES)


def search_municipalities(query: str) -> list[dict[str, str]]:
    """Return deterministic municipality-like records; no external API is called."""
    normalized = query.strip().lower()
    if not normalized:
        return []
    return [
        row
        for row in MUNICIPALITIES
        if normalized in row["name"].lower() or normalized in row["prefecture"].lower()
    ]


def search_transaction_prices(municipality_code: str, year: int) -> list[dict[str, int | str]]:
    """Return a stable fake transaction-price result, or [] when the code is unknown."""
    if municipality_code not in KNOWN_MUNICIPALITY_CODES:
        return []
    return [
        {
            "municipality_code": municipality_code,
            "year": year,
            "price_yen": 52_000_000,
            "property_type": "condominium",
            "source": "mock",
        }
    ]


def search_stations(municipality_code: str) -> list[dict[str, str]]:
    """Return deterministic station-like records, or [] when the code is unknown."""
    return list(STATIONS.get(municipality_code, []))


def dispatch_tool(name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """In-process tool body. FastMCP wrappers and the stub front share this."""
    if name == "find_municipalities":
        return search_municipalities(str(arguments.get("query", "")))
    if name == "find_stations":
        return search_stations(str(arguments.get("municipality_code", "")))
    if name == "find_transaction_prices":
        return search_transaction_prices(
            str(arguments.get("municipality_code", "")),
            int(arguments.get("year", 2025)),
        )
    raise KeyError(name)
