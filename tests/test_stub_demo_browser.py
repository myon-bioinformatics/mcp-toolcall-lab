"""Drive the local, client-side heading-lookup demo with a real browser.

Reusable, reproducible version of the manual Playwright check used while
developing stub_demo.js: write_stub_demo_page() (not the published Pages
index — that report no longer embeds this mock), serve it on a free local
port (never hardcoded -- ports collide across CI runs and local runs alike),
and click through the same cases hand-verified before this file existed --
heading hit, an MCP-shaped prompt labelled (never faked), a miss listing
known headings, and the demo chat_id being minted once, not per turn.
Locator/asset ids come from stub_front.py's own constants, not re-strung
here, so a rename there cannot silently desync this test the way a
hardcoded "#chat-input" would.
"""

from __future__ import annotations

import functools
import http.server
import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mcp_toolcall_lab.stub_front import OWUI_INPUT, OWUI_SEND, write_stub_demo_page

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_static_site(directory: Path) -> Iterator[str]:
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    with sync_playwright() as p:
        launched = p.chromium.launch(executable_path=executable_path)
        yield launched
        launched.close()


@pytest.fixture()
def demo_page(tmp_path_factory: pytest.TempPathFactory, browser):
    out_dir = tmp_path_factory.mktemp("stub-pages-site")
    write_stub_demo_page(out_dir)
    with running_static_site(out_dir) as url:
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url, wait_until="load")
        try:
            yield page, errors
        finally:
            page.close()


def _send(page, text: str) -> None:
    page.fill(f"#{OWUI_INPUT}", text)
    page.click(f"#{OWUI_SEND}")


def _last_turn_text(page) -> str:
    return page.locator(".stub-demo-turn").last.inner_text()


def test_chat_id_is_minted_once_and_stable_across_turns(demo_page) -> None:
    page, errors = demo_page
    chat_id_line = page.locator('[data-testid="stub-demo-chat-id"]')
    first = chat_id_line.inner_text()
    assert "chat_" in first
    _send(page, "Yokohama")
    assert chat_id_line.inner_text() == first, "chat_id must be minted once at mount, not per turn"
    assert errors == []


def test_heading_hit_returns_a_real_body(demo_page) -> None:
    page, errors = demo_page
    _send(page, "Yokohama")
    text = _last_turn_text(page)
    assert "HEADING_HIT" in text
    assert "14109" in text  # Yokohama's mock municipality code, from the corpus body
    assert errors == []


def test_mcp_pattern_is_labelled_not_faked(demo_page) -> None:
    page, errors = demo_page
    _send(page, "Find municipalities named Yokohama")
    text = _last_turn_text(page)
    assert "MCP_PATTERN" in text
    assert "find_municipalities" in text
    assert "no MCP server behind it" in text
    assert "14109" not in text, "a static page with no MCP client must never fabricate a result row"
    assert errors == []


def test_miss_lists_known_headings(demo_page) -> None:
    page, errors = demo_page
    _send(page, "this heading does not exist xyz")
    text = _last_turn_text(page)
    assert "HEADING_MISS" in text
    assert "Yokohama" in text  # one of the corpus's own known headings
    assert errors == []
