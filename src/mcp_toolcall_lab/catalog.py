"""Pure-Python mock data used by the FastMCP server and unit tests.

A valid call that matches nothing returns an empty list. That is not an error.
Protocol problems (unknown tool, invalid arguments, timeout) are errors.

``fetch_wikipedia_section`` and ``fetch_wikipedia_article`` are the tools
below that are NOT deterministic offline mocks -- they are registered
here (same ``AVAILABLE_TOOLS``/``TOOL_DESCRIPTIONS``/``dispatch_tool()``
surface as every other tool, so they are callable the same way over real
MCP) but their actual implementation, including the "this makes a real
HTTP call" fact, lives in ``wikipedia_tool.py``, not here -- see that
module's docstring. The list-of-rows contract for
``fetch_wikipedia_section`` is unchanged.
"""

from __future__ import annotations

from typing import Any

from .wikipedia_tool import DEFAULT_LANG, fetch_wikipedia_article, fetch_wikipedia_section
from .pixiv_dictionary_tool import fetch_pixiv_dictionary_article, fetch_pixiv_dictionary_section

AVAILABLE_TOOLS = (
    "find_municipalities",
    "find_transaction_prices",
    "find_stations",
    "fetch_wikipedia_section",
    "fetch_wikipedia_article",
    "fetch_pixiv_dictionary_section",
    "fetch_pixiv_dictionary_article",
)

TOOL_DESCRIPTIONS = {
    "find_municipalities": "Find mock municipalities by name or prefecture.",
    "find_transaction_prices": "Return mock property transaction prices. This never calls a live API.",
    "find_stations": "Find mock stations in a municipality.",
    "fetch_wikipedia_section": (
        "Fetch a real Wikipedia article's sections over HTTP (the one family here that is not a "
        "mock). Omit `heading` to list section titles (a pulldown); pass one to get that "
        "section's body. Unmatched heading is an empty result, not an error. Same article is "
        "cached in-process so heading switches do not refetch."
    ),
    "fetch_wikipedia_article": (
        "Fetch a real Wikipedia article as a MediaWiki plaintext extract (not HTML). Returns "
        "canonical_title, extract, and headings from that same fetch. In-process TTL/LRU cache "
        "reuses the extract when only the heading changes."
    ),
    "fetch_pixiv_dictionary_section": (
        "Fetch one public pixiv Encyclopedia article HTML page, normalize it through vendored "
        "markdown.py, and return its hierarchical heading list or one selected section. "
        "The cached article is reused when only the heading changes."
    ),
    "fetch_pixiv_dictionary_article": (
        "Fetch one public pixiv Encyclopedia article HTML page and normalize it through vendored "
        "markdown.py. Returns canonical_title, Markdown, hierarchical headings and source_url."
    ),
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


def dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
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
    if name == "fetch_wikipedia_section":
        return fetch_wikipedia_section(
            str(arguments.get("title", "")),
            str(arguments.get("heading", "")),
            lang=str(arguments.get("lang") or DEFAULT_LANG),
        )
    if name == "fetch_wikipedia_article":
        return fetch_wikipedia_article(
            str(arguments.get("title", "")),
            lang=str(arguments.get("lang") or DEFAULT_LANG),
        )
    if name == "fetch_pixiv_dictionary_section":
        return fetch_pixiv_dictionary_section(
            str(arguments.get("title", "")),
            str(arguments.get("heading", "")),
        )
    if name == "fetch_pixiv_dictionary_article":
        return fetch_pixiv_dictionary_article(str(arguments.get("title", "")))
    raise KeyError(name)
