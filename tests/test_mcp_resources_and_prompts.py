"""Test FastMCP prompts and resources capabilities."""

from __future__ import annotations

import json
import pytest

from mcp_toolcall_lab.server import create_mcp


@pytest.mark.asyncio
async def test_mcp_prompts_and_resources_are_advertised():
    mcp = create_mcp()

    prompts = await mcp.list_prompts()
    assert len(prompts) >= 1
    prompt_names = [p.name for p in prompts]
    assert "strict_tool_selection" in prompt_names

    rendered = await mcp.render_prompt("strict_tool_selection", {"query": "Find Yokohama"})
    prompt_text = rendered.messages[0].content.text
    assert "MUST ONLY call tools" in prompt_text
    assert "Find Yokohama" in prompt_text

    resources = await mcp.list_resources()
    resource_uris = [str(r.uri) for r in resources]
    assert "resource://catalog/tools" in resource_uris
    assert "resource://prompts/strict_tool_selection" in resource_uris

    catalog_data = await mcp.read_resource("resource://catalog/tools")
    content_str = catalog_data.contents[0].content
    parsed = json.loads(content_str)
    assert "find_municipalities" in parsed["available_tools"]
