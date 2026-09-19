"""chat-e2e: Open WebUI, driven end-to-end through its real chat UI.

Distinct from tests/test_browser_fetch_protocol.py: that module proves a
browser's own `fetch()` can reach the mock MCP server directly, with no chat
product involved at all. This module proves the *product* -- typing into
Open WebUI's real chat screen and pressing Send produces an assistant reply
-- covering issue #14's chat-e2e minimum bar:

  1. UI starts
  2. chat page opens
  3. prompt can be entered
  4. Send can be pressed
  5. assistant response can be awaited

Tool selection (chat UI -> tool selection -> mock MCP -> tool result ->
assistant response) is a follow-up extension of this same test once Open
WebUI's mcp-mock tool-server connection can be registered without a human
clicking through its admin settings -- see docker-compose.yml's `openwebui`
service and docs/e2e_foundation.md.

Selectors below are best-effort against Open WebUI's documented/typical
markup, written without a live instance to check them against in the
environment that produced this PR -- expect to adjust them once this lane
runs for real (see docs/e2e_foundation.md).
"""

from __future__ import annotations

import pytest

from tests.e2e._util import base_url, require_reachable

pytestmark = pytest.mark.chat_e2e

BASE_URL = base_url("OPENWEBUI_BASE_URL", "http://127.0.0.1:3000")


@pytest.fixture(autouse=True)
def _skip_if_unreachable():
    require_reachable(BASE_URL, "openwebui")


def test_chat_ui_accepts_a_prompt_and_returns_an_assistant_response(page):
    page.goto(BASE_URL, wait_until="load")

    composer = page.locator("#chat-input, textarea, [contenteditable='true']").first
    composer.wait_for(state="visible", timeout=30_000)
    composer.click()
    composer.type("Hello from the chat-e2e lane")

    try:
        send_button = page.get_by_role("button", name="Send message")
        send_button.wait_for(state="visible", timeout=5_000)
    except Exception:
        send_button = page.locator("button[type='submit']").first
    send_button.click()

    assistant_message = page.locator(
        "[data-message-role='assistant'], .message.assistant, .chat-assistant"
    ).first
    assistant_message.wait_for(state="visible", timeout=60_000)
    assert assistant_message.inner_text().strip() != ""
