"""chat-e2e: LibreChat, driven end-to-end through its real chat UI.

Same minimum bar as tests/e2e/test_openwebui_chat_e2e.py -- see that module's
docstring for the full rationale and the shared caveat about selectors being
best-effort/unverified in the environment that produced this PR.
"""

from __future__ import annotations

import pytest

from tests.e2e._util import base_url, require_reachable

pytestmark = pytest.mark.chat_e2e

BASE_URL = base_url("LIBRECHAT_BASE_URL", "http://127.0.0.1:3080")


@pytest.fixture(autouse=True)
def _skip_if_unreachable():
    require_reachable(BASE_URL, "librechat")


def test_chat_ui_accepts_a_prompt_and_returns_an_assistant_response(page):
    page.goto(BASE_URL, wait_until="load")

    composer = page.locator("#prompt-textarea, textarea, [contenteditable='true']").first
    composer.wait_for(state="visible", timeout=30_000)
    composer.click()
    composer.type("Hello from the chat-e2e lane")

    try:
        send_button = page.get_by_test_id("send-button")
        send_button.wait_for(state="visible", timeout=5_000)
    except Exception:
        send_button = page.locator("button[type='submit']").first
    send_button.click()

    assistant_message = page.locator(
        "[data-testid='message-assistant'], .message.assistant, .chat-assistant"
    ).first
    assistant_message.wait_for(state="visible", timeout=60_000)
    assert assistant_message.inner_text().strip() != ""
