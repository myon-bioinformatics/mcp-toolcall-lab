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

import functools, http.server, os, socket, threading
from collections.abc import Iterator; from contextlib import contextmanager; from pathlib import Path

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
    _send(page, "Exampleville")
    assert chat_id_line.inner_text() == first, "chat_id must be minted once at mount, not per turn"
    assert errors == []


def test_heading_hit_returns_a_real_body(demo_page) -> None:
    page, errors = demo_page
    _send(page, "Exampleville")
    text = _last_turn_text(page)
    assert "HEADING_HIT" in text
    assert "90001" in text  # Exampleville's mock municipality code, from the corpus body
    assert errors == []


def test_mcp_pattern_is_labelled_not_faked(demo_page) -> None:
    page, errors = demo_page
    _send(page, "Find municipalities named Exampleville")
    text = _last_turn_text(page)
    assert "MCP_PATTERN" in text
    assert "find_municipalities" in text
    assert "no MCP server behind it" in text
    assert "90001" not in text, "a static page with no MCP client must never fabricate a result row"
    assert errors == []


def test_miss_lists_known_headings(demo_page) -> None:
    page, errors = demo_page
    _send(page, "this heading does not exist xyz")
    text = _last_turn_text(page)
    assert "HEADING_MISS" in text
    assert "Exampleville" in text  # one of the corpus's own known headings
    assert errors == []


def _assert_readable_colors(locator) -> None:
    """Check actual cascaded foreground/background, not just CSS source tokens."""
    colors = locator.evaluate("""element => {
      const style = getComputedStyle(element);
      return {foreground: style.color, background: style.backgroundColor};
    }""")
    import re

    def luminance(color):
        channels = [float(value) for value in re.findall(r"[\d.]+", color)]
        assert len(channels) in (3, 4), color
        assert len(channels) == 3 or channels[3] == 1, colors
        linear = [value / 255 / 12.92 if value / 255 <= 0.04045
                  else ((value / 255 + 0.055) / 1.055) ** 2.4 for value in channels[:3]]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    values = sorted(luminance(color) for color in colors.values())
    ratio = (values[1] + 0.05) / (values[0] + 0.05)
    assert ratio >= 4.5, (colors, ratio)


def test_published_pages_and_shared_diagnostics_keep_readable_colors(tmp_path, browser):
    from mcp_toolcall_lab.stub_front import write_pages
    from mcp_toolcall_lab import mcp_toolcall_lab_repository_diagnostics as diagnostics

    write_pages(tmp_path)
    diagnostics.write_pages(tmp_path, probe=False)
    source = (Path(__file__).resolve().parents[1] / "fixtures/pokeapi/ditto.json").read_text()
    with running_static_site(tmp_path) as url:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(url + "#pixiv", wait_until="load")
            command = page.locator("#pixiv pre.ui-output").last
            assert command.is_visible()
            _assert_readable_colors(command)

            # Preserve main's shared source panel and repeatable hash navigation.
            page.get_by_test_id("pages-nav-pokemon").click()
            page.locator("#pokemon-text").fill(source)
            page.locator("#source-pokemon [data-source-extract]").click()
            output = page.locator("#source-pokemon [data-source-output]")
            assert output.is_visible() and "ditto (#132)" in output.inner_text()
            _assert_readable_colors(output)
            page.get_by_test_id("pages-nav-pixiv").click()
            assert command.is_visible()
            page.get_by_test_id("pages-nav-pokemon").click()
            assert output.is_visible() and "ditto (#132)" in output.inner_text()

            page.goto(url + diagnostics.PAGE_NAME, wait_until="networkidle")
            card = page.locator(".repo-diagnostics")
            card.wait_for(state="visible")
            _assert_readable_colors(card)
            assert errors == []
        finally:
            page.close()
