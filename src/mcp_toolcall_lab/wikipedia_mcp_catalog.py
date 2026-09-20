"""Export FastMCP Wikipedia tool specs for the Deno Streamable HTTP endpoint.

The Deno server is keyless and Wikipedia-only. Its ``tools/list`` payload
must not be hand-copied: this module reads ``create_mcp().list_tools()``
and writes ``deploy/wikipedia-mcp/catalog.generated.json``. CI asserts the
file still matches the live FastMCP catalog.

    python -m mcp_toolcall_lab.wikipedia_mcp_catalog
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.catalog import TOOL_DESCRIPTIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "deploy" / "wikipedia-mcp" / "catalog.generated.json"
WIKIPEDIA_TOOL_NAMES = ("fetch_wikipedia_article", "fetch_wikipedia_section")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def spec_from_tool(tool: Any) -> dict[str, Any]:
    """Normalize one FastMCP / MCP SDK tool to the advertised wire surface."""
    schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None)
    output = getattr(tool, "outputSchema", None)
    if output is None:
        output = getattr(tool, "output_schema", None)
    spec: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": _jsonable(schema),
    }
    if output is not None:
        spec["outputSchema"] = _jsonable(output)
    return spec


def wikipedia_specs_from_tools(tools: Any) -> list[dict[str, Any]]:
    specs = [spec_from_tool(tool) for tool in tools if getattr(tool, "name", None) in WIKIPEDIA_TOOL_NAMES]
    specs.sort(key=lambda item: str(item["name"]))
    names = [item["name"] for item in specs]
    if tuple(names) != tuple(sorted(WIKIPEDIA_TOOL_NAMES)):
        raise ValueError(f"expected Wikipedia tools {WIKIPEDIA_TOOL_NAMES}, got {names}")
    for spec in specs:
        if spec["description"] != TOOL_DESCRIPTIONS[spec["name"]]:
            raise ValueError(f"description drift for {spec['name']}")
    return specs


async def load_wikipedia_specs() -> list[dict[str, Any]]:
    from mcp_toolcall_lab.server import create_mcp

    mcp = create_mcp()
    tools = await mcp.list_tools()
    return wikipedia_specs_from_tools(tools)


def catalog_document(specs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "repository": "https://github.com/myon-bioinformatics/mcp-toolcall-lab",
        "source": "mcp_toolcall_lab.server.create_mcp list_tools",
        "tools": specs,
    }


def write_catalog(path: Path | None = None, *, specs: list[dict[str, Any]] | None = None) -> Path:
    target = path or CATALOG_PATH
    payload = catalog_document(specs if specs is not None else asyncio.run(load_wikipedia_specs()))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main() -> None:
    path = write_catalog()
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
