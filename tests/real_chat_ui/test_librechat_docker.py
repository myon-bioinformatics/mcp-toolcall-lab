"""Real LibreChat in Docker: type, click Send, then record whether MCP returned.

Selectors and login live in ``mcp_toolcall_lab.frontends`` /
``mcp_toolcall_lab.chat_ui`` so Playwright, CLI one-liners, and this test
share one definition.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from mcp_toolcall_lab.antipatterns import VERDICT_PASS, write_observation
from mcp_toolcall_lab.chat_ui import (
    classify_send_result,
    enable_mcp_picker,
    login,
    type_and_send,
    wait_for_assistant,
)
from mcp_toolcall_lab.frontends import LIBRECHAT
from mcp_toolcall_lab.trace_probe import snapshot_trace

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from mcp_toolcall_lab.chat_ui import _register  # noqa: E402

LIBRECHAT_BASE_URL = os.environ.get("LIBRECHAT_BASE_URL", "").rstrip("/")
MCP_LOG = Path(os.environ.get("MCP_TOOLCALL_LOG", "test-results/mcp-toolcalls.jsonl"))
OPENAI_LOG = Path(os.environ.get("OPENAI_MOCK_LOG", "test-results/openai-mock.jsonl"))
OBSERVED = Path(os.environ.get("ANTIPATTERN_LOG", "test-results/antipatterns.jsonl"))
CHAT_MESSAGE = os.environ.get("LIBRECHAT_CHAT_MESSAGE", LIBRECHAT.sample_prompt)

pytestmark = [
    pytest.mark.real_chat_ui,
    pytest.mark.skipif(
        not LIBRECHAT_BASE_URL,
        reason="LIBRECHAT_BASE_URL not set — only docker/librechat-smoke or "
        "the librechat-docker-smoke workflow sets this",
    ),
]

EMAIL = os.environ.get("LIBRECHAT_SMOKE_EMAIL", f"smoke-{uuid.uuid4().hex[:8]}@lab.example")
PASSWORD = os.environ.get("LIBRECHAT_SMOKE_PASSWORD", "LabSmoke123!")
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


def test_librechat_types_message_and_clicks_send(page) -> None:
    _register(LIBRECHAT, LIBRECHAT_BASE_URL, EMAIL, PASSWORD, NAME)
    assert login(page, LIBRECHAT, LIBRECHAT_BASE_URL, EMAIL, PASSWORD)
    enable_mcp_picker(page, LIBRECHAT)
    flags = type_and_send(page, LIBRECHAT, CHAT_MESSAGE)
    assert flags["input_found"]
    assert flags["send_clicked"]


def test_mcp_return_or_record_antipattern(page) -> None:
    _register(LIBRECHAT, LIBRECHAT_BASE_URL, EMAIL, PASSWORD, NAME)
    logged_in = login(page, LIBRECHAT, LIBRECHAT_BASE_URL, EMAIL, PASSWORD)
    flags: dict = {"input_found": False, "send_clicked": False, "logged_in": logged_in}
    ui_text = ""
    if logged_in:
        enable_mcp_picker(page, LIBRECHAT)
        try:
            flags.update(type_and_send(page, LIBRECHAT, CHAT_MESSAGE))
            flags["assistant_visible"] = wait_for_assistant(page, LIBRECHAT)
        except Exception:
            flags["assistant_visible"] = False
        ui_text = page.locator("body").inner_text()
        flags["page_url"] = page.url
    flags["ui_text_tail"] = ui_text
    classified = classify_send_result(
        frontend=LIBRECHAT,
        flags=flags,
        mcp_log=MCP_LOG,
        openai_log=OPENAI_LOG,
    )
    page_url = flags.get("page_url") or LIBRECHAT_BASE_URL
    trace = snapshot_trace(
        mcp_log=MCP_LOG,
        openai_log=OPENAI_LOG,
        page_url=page_url,
        text=ui_text,
    )
    record = write_observation(
        OBSERVED,
        observation={
            **classified,
            "chat_message": CHAT_MESSAGE,
            "client": LIBRECHAT.id,
            "url": LIBRECHAT_BASE_URL,
            "page_url": page_url,
            "trace": trace,
        },
        source="librechat-docker-playwright",
    )
    assert record["verdict"] in {VERDICT_PASS, "ANTIPATTERN"}
    assert OBSERVED.is_file()
    if classified["verdict"] != VERDICT_PASS:
        print(f"recorded anti-pattern {classified['antipattern_id']}: {classified['detail']}")
