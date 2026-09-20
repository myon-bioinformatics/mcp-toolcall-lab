"""Stdlib stub /wiki form: escaped extract + server-rendered heading select."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from mcp_toolcall_lab.stub_front import (
    DEFAULT_CORPUS,
    StubState,
    WIKI_EXTRACT_NOTE,
    WIKI_PAGES_DISCLAIMER,
    load_corpus,
    make_handler,
    render_wiki_page,
    write_pages,
)
from mcp_toolcall_lab.wikipedia_tool import FIXTURE_ENV

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "wikipedia" / "yokohama_extract.json"


def test_wiki_form_is_empty_until_a_title_is_submitted() -> None:
    html = render_wiki_page()
    assert 'data-testid="wiki-title"' in html
    assert 'method="get" action="/wiki"' in html
    assert WIKI_PAGES_DISCLAIMER in html
    assert 'data-testid="wiki-extract"' not in html
    assert "markdown_to_html" not in html


def test_wiki_title_shows_escaped_plaintext_extract(monkeypatch) -> None:
    monkeypatch.setenv(FIXTURE_ENV, str(FIXTURE))
    html = render_wiki_page(title="Yokohama")
    assert 'data-testid="wiki-extract"' in html
    assert "code 14109" in html
    assert "== Geography ==" in html
    assert WIKI_EXTRACT_NOTE in html
    assert 'data-cache="miss"' in html
    assert 'data-testid="wiki-heading-select"' in html
    assert ">Geography<" in html
    assert 'data-testid="wiki-canonical-title">Yokohama</strong>' in html
    # Second render of the same title is a cache hit and still does not refetch.
    html_hit = render_wiki_page(title="Yokohama")
    assert 'data-cache="hit"' in html_hit


def test_wiki_heading_shows_section_body_without_html_injection(monkeypatch) -> None:
    monkeypatch.setenv(FIXTURE_ENV, str(FIXTURE))
    html = render_wiki_page(title="Yokohama", heading="Geography")
    assert 'data-testid="wiki-section"' in html
    assert "Kanagawa Prefecture" in html
    assert 'value="Geography" selected' in html
    assert "<script>" not in html


def test_wiki_escapes_extract_that_looks_like_html(monkeypatch) -> None:
    import io

    from mcp_toolcall_lab import wikipedia_tool as wt

    payload = {
        "batchcomplete": "",
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "ns": 0,
                    "title": "Yokohama",
                    "extract": 'Lead <script>alert("xss")</script>\n\n== Geography ==\n<body onload=x>\n',
                }
            }
        },
    }

    class _FakeResponse:
        def __init__(self) -> None:
            self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(wt.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse())
    html = render_wiki_page(title="Yokohama", heading="Geography")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;body onload=x&gt;" in html
    assert 'data-testid="wiki-section"' in html


def test_pages_tree_hosts_browser_mediawiki_not_local_wiki_or_try_it(tmp_path: Path) -> None:
    index = write_pages(tmp_path / "site")
    html = index.read_text(encoding="utf-8")
    assert "Browser → MediaWiki API (not MCP)" in html
    assert "stub_front serve" in html
    assert 'data-testid="pages-wiki-app"' in html
    assert 'data-testid="pages-wiki-title"' in html
    assert 'data-testid="pages-wiki-form"' in html
    assert 'src="pages-wiki.js"' in html
    assert 'data-testid="wiki-title"' not in html
    assert 'action="/wiki"' not in html
    assert 'href="/wiki"' not in html
    assert 'href="#wiki"' in html
    assert "Try it (static, no MCP)" not in html
    assert "mcpToolcallLabStubDemo" not in html


def test_composer_page_links_to_wiki_without_implying_pages_backend() -> None:
    from mcp_toolcall_lab.stub_front import _page

    html = _page("chat_" + "c" * 24, [], sections=load_corpus(DEFAULT_CORPUS))
    assert 'href="/wiki"' in html
    assert "wikipedia.org" not in html
    assert "this server only" in html


def test_wiki_route_serves_the_fixture_article(monkeypatch) -> None:
    monkeypatch.setenv(FIXTURE_ENV, str(FIXTURE))
    state = StubState(sections=load_corpus(DEFAULT_CORPUS), mcp_url=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/wiki?title=Yokohama", timeout=5) as response:
            assert response.status == 200
            html = response.read().decode("utf-8")
        assert "code 14109" in html
        assert 'data-testid="wiki-heading-select"' in html
        with urlopen(
            f"http://{host}:{port}/wiki?title=Yokohama&heading=Geography", timeout=5
        ) as response:
            selected = response.read().decode("utf-8")
        assert "Kanagawa Prefecture" in selected
        assert 'data-cache="hit"' in selected
    finally:
        server.shutdown()
        server.server_close()


def test_screenshot_script_invokes_playwright_cli() -> None:
    source = (ROOT / "scripts" / "wikipedia_article_screenshot.py").read_text(encoding="utf-8")
    assert '"playwright"' in source
    assert '"screenshot"' in source
    assert "--full-page" in source
    assert "wiki-title-entered.png" in source
    assert "wiki-heading-selected.png" in source
    workflow = (ROOT / ".github" / "workflows" / "wikipedia-article-screenshot.yml").read_text(
        encoding="utf-8"
    )
    assert "wikipedia_article_screenshot.py" in workflow
    assert "yokohama_extract.json" in workflow
    assert "workflow_dispatch" in workflow
    assert "pytest -q" in workflow
    assert "live_wikipedia" in workflow
