"""Playwright fixtures for the chat-e2e / browser-smoke lanes only.

Every test under tests/e2e/ skips itself when Playwright isn't installed. It
ships in the `browser-test` extra, not `test` -- so under the normal-PR
lane's `pip install -e ".[test]"` this whole package is inert, the same way
tests/test_browser_fetch_protocol.py already is with its own
`pytest.importorskip`. That helper isn't used here directly: raising a skip
from inside conftest.py's own import (rather than a test module's) risks
pytest treating it as a collection error instead of a skip, so the missing
import is caught and turned into a plain `pytest.skip()` call inside the
`browser` fixture instead, where a skip is always safe.
See docs/e2e_foundation.md for how to actually run this lane.
"""

from __future__ import annotations

import os

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@pytest.fixture(scope="module")
def browser():
    if sync_playwright is None:
        pytest.skip("playwright is not installed -- pip install -e '.[browser-test]' and playwright install chromium")
    executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    with sync_playwright() as p:
        launched = p.chromium.launch(executable_path=executable_path)
        yield launched
        launched.close()


@pytest.fixture()
def page(browser):
    pg = browser.new_page()
    try:
        yield pg
    finally:
        pg.close()
