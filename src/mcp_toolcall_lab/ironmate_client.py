"""Thin Ironmate MCP boundary built on the lab's stdlib MCP session."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class McpSession(Protocol):
    def initialize(self) -> dict[str, Any]: ...
    def list_tools(self) -> dict[str, Any]: ...
    def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...


def normalize_catalog(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only the stable MCP catalog surface used by the snapshot."""
    tools = response.get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise ValueError("Ironmate tools/list response has no result.tools list")
    out = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("Ironmate catalog contains an invalid tool")
        out.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {"type": "object"}),
        })
    return sorted(out, key=lambda item: item["name"])


@dataclass
class IronmateClient:
    """No Ironmate business logic: only normal MCP discovery/call delegation."""
    session: McpSession

    def discover(self) -> list[dict[str, Any]]:
        self.session.initialize()
        return normalize_catalog(self.session.list_tools())

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.session.call_tool(name, arguments)
