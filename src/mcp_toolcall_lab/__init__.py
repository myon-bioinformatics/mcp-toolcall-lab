"""Mock MCP tools and helpers for tool-calling experiments."""

from .catalog import AVAILABLE_TOOLS, TOOL_DESCRIPTIONS

__all__ = [
    "AVAILABLE_TOOLS",
    "TOOL_DESCRIPTIONS",
    "create_mcp",
    "mcp",
    "run_server",
]


def __getattr__(name: str):
    """Lazy FastMCP server import so the stdlib stub image need not install it."""
    if name in {"create_mcp", "mcp", "run_server"}:
        from .server import create_mcp, mcp, run_server

        return {"create_mcp": create_mcp, "mcp": mcp, "run_server": run_server}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
