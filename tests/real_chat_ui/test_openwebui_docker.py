"""Real Open WebUI in Docker: type, click Send, then assert the product wire loop.

Selectors and login live in ``mcp_toolcall_lab.frontends`` /
``mcp_toolcall_lab.chat_ui``. This module does not invent a second protocol:
it reads the OpenAI mock log and the MCP JSONL the mocks already write.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from mcp_toolcall_lab.antipatterns import write_observation
from mcp_toolcall_lab.chat_ui import (
    classify_send_result,
    enable_mcp_picker,
    login,
    type_and_send,
    wait_for_assistant,
)
from mcp_toolcall_lab.frontends import OPENWEBUI
from mcp_toolcall_lab.mock.common import read_jsonl
from mcp_toolcall_lab.trace_probe import snapshot_trace
from tests.real_chat_ui.openwebui_loop import assert_openwebui_tool_loop

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

OPEN_WEBUI_BASE_URL = os.environ.get("OPEN_WEBUI_BASE_URL", "").rstrip("/")
MCP_LOG = Path(os.environ.get("MCP_TOOLCALL_LOG", "test-results/mcp-toolcalls.jsonl"))
OPENAI_LOG = Path(os.environ.get("OPENAI_MOCK_LOG", "test-results/openai-mock.jsonl"))
OBSERVED = Path(os.environ.get("ANTIPATTERN_LOG", "test-results/antipatterns.jsonl"))
CHAT_MESSAGE = os.environ.get("OPENWEBUI_CHAT_MESSAGE", OPENWEBUI.sample_prompt)

pytestmark = [
    pytest.mark.real_chat_ui,
    pytest.mark.skipif(
        not OPEN_WEBUI_BASE_URL,
        reason="OPEN_WEBUI_BASE_URL not set — only docker/openwebui-smoke or "
        "the openwebui-docker-smoke workflow sets this",
    ),
]

EMAIL = os.environ.get("OPENWEBUI_SMOKE_EMAIL", f"smoke-{uuid.uuid4().hex[:8]}@lab.example")
PASSWORD = os.environ.get("OPENWEBUI_SMOKE_PASSWORD", "LabSmoke123!")
NAME = "Lab Smoke"


@pytest.fixture(scope="module")
def browser():
    executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(executable_path=executable_path)
        yield launched
        launched.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    try:
        yield pg
    finally:
        pg.close()
        context.close()


def test_openwebui_types_message_and_clicks_send(page) -> None:
    assert login(page, OPENWEBUI, OPEN_WEBUI_BASE_URL, EMAIL, PASSWORD)
    enable_mcp_picker(page, OPENWEBUI)
    flags = type_and_send(page, OPENWEBUI, CHAT_MESSAGE)
    assert flags["input_found"]
    assert flags["send_clicked"]


def test_openwebui_toolcall_loop_and_id_continuity(page) -> None:
    logged_in = login(page, OPENWEBUI, OPEN_WEBUI_BASE_URL, EMAIL, PASSWORD)
    flags: dict = {"input_found": False, "send_clicked": False, "logged_in": logged_in}
    ui_text = ""
    if logged_in:
        enable_mcp_picker(page, OPENWEBUI)
        before = 0
        if OPENWEBUI.response.container is not None:
            before = page.locator(OPENWEBUI.response.container.css()).count()
        flags.update(type_and_send(page, OPENWEBUI, CHAT_MESSAGE))
        # A prior Send (or leftover chat) can already have #response-content-container.
        flags["assistant_visible"] = wait_for_assistant(
            page,
            OPENWEBUI,
            timeout_ms=90_000,
            min_count=before + 1,
            text_in_last=OPENWEBUI.sample_result_fragment,
        )
        ui_text = page.locator("body").inner_text()
        flags["page_url"] = page.url
        flags["ui_text_tail"] = ui_text
    else:
        flags["assistant_visible"] = False
        flags["page_url"] = OPEN_WEBUI_BASE_URL
        flags["ui_text_tail"] = ""

    classified = classify_send_result(
        frontend=OPENWEBUI,
        flags=flags,
        mcp_log=MCP_LOG,
        openai_log=OPENAI_LOG,
    )
    page_url = flags.get("page_url") or OPEN_WEBUI_BASE_URL
    openai_events = read_jsonl(OPENAI_LOG)
    mcp_events = read_jsonl(MCP_LOG)
    loop = assert_openwebui_tool_loop(
        openai_events=openai_events,
        mcp_events=mcp_events,
        page_url=page_url,
        ui_text=ui_text,
        expected_fragment=OPENWEBUI.sample_result_fragment,
    )
    trace = snapshot_trace(
        mcp_log=MCP_LOG,
        openai_log=OPENAI_LOG,
        page_url=page_url,
        text=ui_text,
    )
    write_observation(
        OBSERVED,
        observation={
            **classified,
            **loop,
            "chat_message": CHAT_MESSAGE,
            "client": OPENWEBUI.id,
            "url": OPEN_WEBUI_BASE_URL,
            "page_url": page_url,
            "trace": trace,
        },
        source="openwebui-docker-playwright",
    )
    assert classified["verdict"] == "PASS", classified
    assert flags["send_clicked"]
    assert flags["assistant_visible"]
    assert OBSERVED.is_file()
