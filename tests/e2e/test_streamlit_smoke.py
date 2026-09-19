"""Manual/limited browser smoke for the Streamlit reference client — issue
#14's third lane: open app, enter prompt, submit, render result. See
tests/e2e/test_gradio_smoke.py for the same rationale; Streamlit is likewise a
reference/diagnostic client (see docs/e2e_foundation.md's "Client roles").

Not wired into CI's default run — meant for `workflow_dispatch` or manual use
against docker-compose.yml's `streamlit` service. Skips when playwright isn't
installed or STREAMLIT_URL isn't reachable.
"""

from __future__ import annotations

import os

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from tests.e2e._reachability import is_reachable  # noqa: E402

STREAMLIT_URL = os.environ.get("STREAMLIT_URL", "http://127.0.0.1:8501")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not is_reachable(STREAMLIT_URL), reason=f"{STREAMLIT_URL} is not reachable"),
]


def test_streamlit_smoke_submit_renders_a_result():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(STREAMLIT_URL, wait_until="load")

            page.get_by_label("Prompt (free text, recorded but not sent to any model)").fill(
                "where is Yokohama"
            )

            page.get_by_test_id("stSelectbox").click()
            page.get_by_role("option", name="find_municipalities").click()

            page.get_by_label("Arguments (JSON)").fill('{"query": "Yokohama"}')
            page.get_by_role("button", name="Send").click()

            page.wait_for_selector("text=case_id", timeout=15000)
        finally:
            page.close()
            browser.close()
