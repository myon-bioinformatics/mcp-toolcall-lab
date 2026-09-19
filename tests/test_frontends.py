from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS
from mcp_toolcall_lab.chat_ui import describe
from mcp_toolcall_lab.frontends import (
    FRONTENDS,
    LIBRECHAT,
    OPENWEBUI,
    PRODUCT_CLIENTS,
    REFERENCE_CLIENTS,
    STUB,
    catalog,
    get_frontend,
)

ROOT = Path(__file__).resolve().parents[1]


def test_both_clients_are_documented() -> None:
    assert set(PRODUCT_CLIENTS) == {"librechat", "openwebui"}
    assert set(REFERENCE_CLIENTS) == {"stub"}
    assert set(FRONTENDS) == PRODUCT_CLIENTS | REFERENCE_CLIENTS
    assert LIBRECHAT.composer.input.css() == '[data-testid="text-input"]'
    assert LIBRECHAT.composer.send.css() == '[data-testid="send-button"]'
    assert OPENWEBUI.composer.input.css() == "#chat-input"
    assert OPENWEBUI.composer.send.css() == "#send-message-button"
    assert OPENWEBUI.response.container is not None
    assert OPENWEBUI.response.container.css() == "#response-content-container"


def test_librechat_mcp_tool_key_and_compose_dns() -> None:
    assert LIBRECHAT.mcp.tool_key("find_municipalities") == "find_municipalities_mcp_lab"
    assert LIBRECHAT.mcp.compose_mcp_url == "http://mcp-mock:8000/mcp"
    assert OPENWEBUI.mcp.tool_key("find_municipalities") == "lab_find_municipalities"
    assert OPENWEBUI.mcp.compose_openai_url == "http://openai-mock:8090/v1"
    assert OPENWEBUI.compose_file == "docker/openwebui-smoke/docker-compose.yml"
    assert set(LIBRECHAT.advertised_tools) == set(AVAILABLE_TOOLS)


def test_aliases() -> None:
    assert get_frontend("owui").id == "openwebui"
    assert get_frontend("lc").id == "librechat"
    assert get_frontend("stubfront").id == "stub"
    assert STUB.role == "reference"
    assert STUB.composer.input.css() == LIBRECHAT.composer.input.css()


def test_catalog_and_describe_are_json() -> None:
    dumped = catalog()
    assert "librechat" in dumped["clients"]
    assert "openwebui" in dumped["clients"]
    assert "send_librechat" in dumped["one_liners"]
    assert "trace_probe" in dumped["one_liners"]
    assert "stub_front_turn" in dumped["one_liners"]
    assert dumped["roles"]["reference"] == ["stub"]
    summary = describe("librechat")
    assert summary["composer_input"] == '[data-testid="text-input"]'
    assert any("chat_ui send" in line for line in summary["one_liners"])
    assert any("trace_probe" in line for line in summary["one_liners"])


def test_frontends_module_one_liner() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.frontends", "openwebui"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["id"] == "openwebui"
    assert payload["composer"]["input"]["value"] == "chat-input"


def test_chat_ui_describe_one_liner() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.chat_ui", "describe"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert "one_liners" in payload
    assert payload["clients"]["librechat"]["default_url"].startswith("http://")
