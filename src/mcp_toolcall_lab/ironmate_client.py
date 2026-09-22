"""Thin Ironmate MCP boundary built on the lab's stdlib MCP session."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Protocol

from mcp_toolcall_lab.record import EVENT_IRONMATE_CALLER, OUTCOME_ERROR, OUTCOME_SUCCESS, record_call


class McpSession(Protocol):
    def initialize(self) -> dict[str, Any]: ...
    def list_tools(self) -> dict[str, Any]: ...
    def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...


def normalize_catalog(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only the stable MCP catalog surface used by the snapshot."""
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("Ironmate tools/list response has no result.tools list")
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise ValueError("Ironmate tools/list response has no result.tools list")
    out = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("Ironmate catalog contains an invalid tool")
        spec = {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {"type": "object"}),
        }
        if "outputSchema" in tool:
            spec["outputSchema"] = tool["outputSchema"]
        out.append(spec)
    return sorted(out, key=lambda item: item["name"])


@dataclass
class IronmateClient:
    """No Ironmate business logic: only normal MCP discovery/call delegation."""
    session: McpSession

    def discover(self) -> list[dict[str, Any]]:
        self.session.initialize()
        return normalize_catalog(self.session.list_tools())

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Delegate one Ironmate call and record it using the lab's shared trace."""
        meta = {"source": "ironmate-client", **(meta or {})}
        started = time.monotonic()
        try:
            result = self.session.call_tool(name, arguments, meta=meta)
        except Exception as exc:
            record_call(
                tool=name,
                arguments=arguments,
                outcome=OUTCOME_ERROR,
                error=str(exc),
                meta=meta,
                debug=debug,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                event=EVENT_IRONMATE_CALLER,
            )
            raise
        record_call(
            tool=name,
            arguments=arguments,
            outcome=OUTCOME_SUCCESS,
            result=result,
            meta=meta,
            debug=debug,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            event=EVENT_IRONMATE_CALLER,
        )
        return result
