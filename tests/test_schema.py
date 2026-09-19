"""Advertised tool surface must match between package and generated standalone file."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS, TOOL_DESCRIPTIONS
from mcp_toolcall_lab.export import render
from mcp_toolcall_lab.server import create_mcp

LIBRECHAT_RENDER_KWARGS = {"run_command": "librechat_mcp_mock.py", "audience": "LibreChat"}

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOL_SPECS = [
    {
        "name": "fetch_wikipedia_section",
        "description": TOOL_DESCRIPTIONS["fetch_wikipedia_section"],
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "heading": {"type": "string", "default": ""},
            },
            "required": ["title"],
            "type": "object",
        },
    },
    {
        "name": "find_municipalities",
        "description": TOOL_DESCRIPTIONS["find_municipalities"],
        "inputSchema": {
            "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "type": "object",
        },
    },
    {
        "name": "find_stations",
        "description": TOOL_DESCRIPTIONS["find_stations"],
        "inputSchema": {
            "additionalProperties": False,
            "properties": {"municipality_code": {"type": "string"}},
            "required": ["municipality_code"],
            "type": "object",
        },
    },
    {
        "name": "find_transaction_prices",
        "description": TOOL_DESCRIPTIONS["find_transaction_prices"],
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "municipality_code": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["municipality_code", "year"],
            "type": "object",
        },
    },
]


def advertised_specs_from_list_tools(tools) -> list[dict]:
    """Normalize FastMCP or MCP SDK tool objects to the Open WebUI-visible surface."""
    specs = []
    for tool in sorted(tools, key=lambda item: item.name):
        schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None)
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump(mode="json")
        specs.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": schema,
            }
        )
    return specs


async def _advertised_specs(mcp) -> list[dict]:
    return advertised_specs_from_list_tools(await mcp.list_tools())


def _load_standalone(filename: str = "openwebui_mcp_mock.py"):
    path = ROOT / filename
    module_name = filename.removesuffix(".py") + "_under_test"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Must be registered before exec_module(): the standalone file inlines
    # markdown_lib.py's `Section` (a `@dataclass` under this file's own
    # `from __future__ import annotations`), and dataclass's own ClassVar/
    # InitVar detection resolves string annotations via
    # sys.modules[cls.__module__] -- omitting this line makes that lookup
    # find nothing and crash with "'NoneType' object has no attribute
    # '__dict__'" the moment any dataclass in the loaded module is touched.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_standalone_file_is_generated_from_package():
    standalone = (ROOT / "openwebui_mcp_mock.py").read_text(encoding="utf-8")
    assert standalone == render()
    assert "# --- mock/common.py ---" in standalone
    assert "from .mock.common" not in standalone


def test_librechat_standalone_file_is_generated_from_package():
    standalone = (ROOT / "librechat_mcp_mock.py").read_text(encoding="utf-8")
    assert standalone == render(**LIBRECHAT_RENDER_KWARGS)


def test_openwebui_and_librechat_standalone_files_differ_only_in_their_docstring():
    """One mock, two product-named copies -- the actual server code must be identical."""
    owui_lines = (ROOT / "openwebui_mcp_mock.py").read_text(encoding="utf-8").splitlines()
    librechat_lines = (ROOT / "librechat_mcp_mock.py").read_text(encoding="utf-8").splitlines()
    # Lines 1-12 are the "Copy this file ... Run with ..." header docstring, which is
    # the one part that's supposed to differ; everything from the closing `"""` on must match.
    header_end = owui_lines.index('"""', 1) + 1
    assert librechat_lines.index('"""', 1) + 1 == header_end
    assert owui_lines[header_end:] == librechat_lines[header_end:]


async def test_package_and_standalone_advertise_the_same_tool_specs():
    package_specs = await _advertised_specs(create_mcp())
    standalone_specs = await _advertised_specs(_load_standalone().mcp)
    assert package_specs == EXPECTED_TOOL_SPECS
    assert standalone_specs == EXPECTED_TOOL_SPECS
    assert tuple(spec["name"] for spec in package_specs) == tuple(sorted(AVAILABLE_TOOLS))


async def test_librechat_standalone_advertises_the_same_tool_specs():
    librechat_specs = await _advertised_specs(_load_standalone("librechat_mcp_mock.py").mcp)
    assert librechat_specs == EXPECTED_TOOL_SPECS
