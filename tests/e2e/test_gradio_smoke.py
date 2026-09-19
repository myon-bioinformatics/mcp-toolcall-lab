"""browser-smoke: Gradio reference client, driven through its real UI.

Minimal shape per issue #14's "manual / limited browser smoke lane": open
app, enter prompt, submit, render result. Unlike the chat-e2e Open
WebUI/LibreChat selectors, these match this repo's own apps/gradio_app.py
labels -- not a third-party product's markup -- so they are not a guess.
"""

from __future__ import annotations

import pytest

from tests.e2e._util import base_url, require_reachable

pytestmark = pytest.mark.browser_smoke

BASE_URL = base_url("GRADIO_BASE_URL", "http://127.0.0.1:7860")


@pytest.fixture(autouse=True)
def _skip_if_unreachable():
    require_reachable(BASE_URL, "gradio")


def test_open_enter_prompt_submit_and_render_result(page):
    page.goto(BASE_URL, wait_until="load")

    page.get_by_label("Prompt (context only, not sent to a model)").fill("Where is Yokohama?")
    page.get_by_role("button", name="Discover tools").click()

    # allow_custom_value=True on this Dropdown means it also accepts typed text,
    # so this doesn't need to open/click a listbox option.
    page.get_by_label("Tool").fill("find_municipalities")
    page.get_by_label("Arguments (JSON)").fill('{"query": "Yokohama"}')
    page.get_by_role("button", name="Submit").click()

    result = page.get_by_label("Result")
    page.wait_for_function(
        "el => el.value && el.value.trim().length > 0",
        arg=result.element_handle(),
        timeout=15_000,
    )
    assert "Yokohama" in result.input_value()
