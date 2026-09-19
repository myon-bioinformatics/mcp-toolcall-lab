"""Playwright `chat-e2e` scaffold for Open WebUI — a real product under test
(see docs/e2e_foundation.md's "Client roles"), not one of the reference
clients in apps/.

This is the `chat-e2e` lane from issue #14: verify actual chat UI behavior —
UI starts, chat page opens, prompt can be entered, Send can be pressed,
assistant response can be awaited — distinct from
tests/test_browser_fetch_protocol.py, which only proves a browser's fetch()
can reach the mock MCP endpoint directly with no chat UI involved at all.

Meant to run against docker-compose.yml's `openwebui` service (`docker
compose up`, then `OPENWEBUI_URL=... pytest tests/e2e/`), not on every PR —
skips automatically when playwright isn't installed or OPENWEBUI_URL isn't
reachable, so it stays inert everywhere else, same as
tests/test_browser_fetch_protocol.py's own `importorskip`.

Known gap (see docs/e2e_foundation.md): registering mcp-mock as an Open WebUI
tool server is a stored-in-DB admin action with no environment variable
today, so this covers UI input + Send + assistant response only — the
issue's minimum bar — not tool selection yet. Selectors below are best-effort
against Open WebUI's documented/typical markup and have not been verified
against a live instance in this environment; expect to adjust them once run
for real.
"""

from __future__ import annotations

import os

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from tests.e2e._reachability import is_reachable  # noqa: E402

OPENWEBUI_URL = os.environ.get("OPENWEBUI_URL", "http://127.0.0.1:3000")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not is_reachable(OPENWEBUI_URL), reason=f"{OPENWEBUI_URL} is not reachable"),
]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        launched = p.chromium.launch()
        yield launched
        launched.close()


def test_openwebui_chat_ui_accepts_a_prompt_and_returns_a_response(browser):
    page = browser.new_page()
    try:
        page.goto(OPENWEBUI_URL, wait_until="load")

        prompt_box = page.get_by_placeholder("Send a Message")
        prompt_box.wait_for(timeout=15000)
        prompt_box.fill("Hello from chat-e2e")
        page.keyboard.press("Enter")

        page.wait_for_selector(
            "[data-testid='assistant-message'], .prose, .message-content",
            timeout=30000,
        )
    finally:
        page.close()
