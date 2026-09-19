"""browser-smoke: Streamlit reference client, driven through its real UI.

Same minimal shape as tests/e2e/test_gradio_smoke.py -- see that module's
docstring. Selectors match this repo's own apps/streamlit_app.py labels.
"""

from __future__ import annotations

import pytest

from tests.e2e._util import base_url, require_reachable

pytestmark = pytest.mark.browser_smoke

BASE_URL = base_url("STREAMLIT_BASE_URL", "http://127.0.0.1:8501")


@pytest.fixture(autouse=True)
def _skip_if_unreachable():
    require_reachable(BASE_URL, "streamlit")


def test_open_enter_prompt_submit_and_render_result(page):
    page.goto(BASE_URL, wait_until="load")

    page.get_by_role("button", name="Discover tools").click()
    page.get_by_label("Prompt (context only, not sent to a model)").fill("Where is Yokohama?")

    page.get_by_label("Tool").click()
    page.get_by_role("option", name="find_municipalities").click()

    page.get_by_label("Arguments (JSON)").fill('{"query": "Yokohama"}')
    page.get_by_role("button", name="Submit").click()

    page.wait_for_selector("text=Yokohama", timeout=15_000)
