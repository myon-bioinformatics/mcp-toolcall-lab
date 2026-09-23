"""Contracts for live Pixiv evidence in the stub-pages lane."""

from pathlib import Path

from mcp_toolcall_lab.pixiv_dictionary_tool import PixivDictionaryFetchError
from mcp_toolcall_lab.stub_front import pixiv_page_response

ROOT = Path(__file__).resolve().parents[1]


def test_pixiv_adapter_uses_public_article_url_without_fixture_by_default() -> None:
    source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_dictionary_tool.py").read_text(encoding="utf-8")
    assert 'PIXIV_ARTICLE = "https://dic.pixiv.net/a/{title}"' in source
    assert "urllib.request.urlopen(request, timeout=TIMEOUT)" in source
    assert "MCP_TOOLCALL_LAB_PIXIV_FIXTURE" in source


def test_stub_pages_captures_real_pixiv_result_in_chromium_and_webkit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert 'PIXIV_URL="http://127.0.0.1:8765/pixiv?title=NARUTO"' in workflow
    assert "https://dic.pixiv.net/a/NARUTO" in workflow
    assert 'grep -q \'data-testid="pixiv-result"\'' in workflow
    assert '--browser=chromium --device="Desktop Chrome"' in workflow
    assert '--browser=webkit --device="iPhone 13"' in workflow
    assert "pixiv-live-desktop.png" in workflow
    assert "pixiv-live-mobile.png" in workflow
    assert "test -s test-results/pixiv-pages-mobile.png" in workflow
    assert 'od -An -tx1 -N8 test-results/pixiv-pages-mobile.png' in workflow
    assert "test -s test-results/pixiv-live-mobile.png" in workflow
    assert "static WebKit exit status: $WEBKIT_STATUS" in workflow
    assert "live WebKit exit status: $LIVE_WEBKIT_STATUS" in workflow
    assert "pixiv-pages-webkit.log" in workflow
    assert "pixiv-live-webkit.log" in workflow
    assert "HTTP status: $HTTP_STATUS" in workflow
    assert "pixiv-live.html (first 120 lines)" in workflow
    assert "logs --tail=200 stub-front" in workflow
    assert "logs --tail=120 stub-front" not in workflow
    assert "missing pixiv-result marker" in workflow
    assert "missing expected Pixiv source URL" in workflow
    assert workflow.count('"src/mcp_toolcall_lab/pixiv_dictionary_tool.py"') == 2


def test_live_pixiv_fetch_is_split_and_strictly_classified() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "name: Live Pixiv fetch (stub-front -> dic.pixiv.net)" in workflow
    assert "id: pixiv_live" in workflow
    assert "name: Capture live Pixiv screenshots" in workflow
    assert "if: steps.pixiv_live.outputs.live == 'ok'" in workflow
    assert "--max-time 30" in workflow
    assert "--dump-header test-results/pixiv-live.headers" in workflow
    assert "X-Pixiv-Stage" in workflow
    assert "X-Pixiv-Upstream-Status" in workflow
    assert "classification: $CLASSIFICATION" in workflow
    assert "::warning title=Pixiv live skipped: $CLASSIFICATION::$DETAIL" in workflow
    assert "::error title=Pixiv live: $1::$2" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    for category in (
        "stub-unreachable",
        "upstream-rate-limited-",
        "upstream-http-",
        "upstream-network",
        "stub-internal-",
        "bad-request",
        "unexpected-http-",
        "result-marker-missing",
        "source-url-missing",
    ):
        assert category in workflow
    # Only the two explicitly accepted upstream conditions may produce a skip.
    skip_condition = (
        '[ "$CLASSIFICATION" = "upstream-http-403" ] || '
        '[ "$CLASSIFICATION" = "upstream-rate-limited-429" ]'
    )
    assert skip_condition in workflow
    assert workflow.count('echo "live=skipped" >> "$GITHUB_OUTPUT"') == 1
    assert 'echo "live=ok" >> "$GITHUB_OUTPUT"' in workflow


def test_pixiv_error_headers_are_ascii_safe_for_japanese_title(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError(
            f"article {arguments['title']!r} returned HTTP 403",
            stage="upstream_http",
            upstream_status=403,
        )

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, body, headers = pixiv_page_response(title="ナルト")
    assert status == 502
    assert 'data-testid="pixiv-error"' in body
    assert 'data-testid="pixiv-result"' not in body
    assert "X-Pixiv-Error" not in headers
    for value in headers.values():
        value.encode("latin-1")


def test_pixiv_success_without_cache_reports_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_toolcall_lab.stub_front.dispatch_tool",
        lambda name, arguments: {"canonical_title": arguments["title"], "markdown": "ok"},
    )
    status, body, headers = pixiv_page_response(title="NARUTO")
    assert status == 200
    assert headers["X-Pixiv-Cache"] == "unknown"
    assert 'data-testid="pixiv-result"' in body


def test_pixiv_error_is_kept_in_body_not_wire_headers(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError(
            f"article {arguments['title']!r} returned HTTP 403",
            stage="upstream_http",
            upstream_status=403,
        )

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    _status, body, headers = pixiv_page_response(title="テスト記事")
    assert "X-Pixiv-Error" not in headers
    assert "テスト記事" in body
