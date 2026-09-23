"""Contracts for live Pixiv evidence in the stub-pages lane."""

from pathlib import Path

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


def test_stub_pages_splits_live_fetch_from_live_screenshots() -> None:
    """The live fetch (dic.pixiv.net) and the screenshots that depend on it are
    separate steps, so a classified skip/failure in the fetch step is visible on
    its own and the screenshot step can gate on its outcome."""
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "name: Live Pixiv fetch (stub-front -> dic.pixiv.net)" in workflow
    assert "id: pixiv_live" in workflow
    assert "name: Capture live Pixiv screenshots" in workflow
    assert "if: steps.pixiv_live.outputs.live == 'ok'" in workflow
    fetch_index = workflow.index("name: Live Pixiv fetch")
    screenshots_index = workflow.index("name: Capture live Pixiv screenshots")
    assert fetch_index < screenshots_index
    # The static-screenshot step must not depend on the live fetch step.
    static_index = workflow.index("name: Capture Pages screenshots with Playwright CLI")
    assert static_index < fetch_index


def test_stub_pages_live_fetch_classifies_failures_by_layer() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "--dump-header test-results/pixiv-live-headers.txt" in workflow
    assert "--max-time 30" in workflow
    assert "X-Pixiv-Stage:" in workflow
    assert "X-Pixiv-Upstream-Status:" in workflow
    assert "stage: ${STAGE:-none}; upstream: ${UPSTREAM_STATUS:-none}" in workflow
    assert "grep 'pixiv request'" in workflow
    assert "::warning::" in workflow
    assert 'echo "live=skipped" >> "$GITHUB_OUTPUT"' in workflow
    assert 'echo "live=ok" >> "$GITHUB_OUTPUT"' in workflow
    # Only 403/upstream_http and 429/upstream_http are ever treated as a skip;
    # every other status (503, 404, internal errors, anything unexpected)
    # keeps failing the step. Two skip branches, no more.
    assert workflow.count("PIXIV_SKIP=1") == 2
    assert '"$HTTP_STATUS" = "502" ] && [ "$STAGE" = "upstream_http" ] && [ "$UPSTREAM_STATUS" = "403"' in workflow
    assert '"$HTTP_STATUS" = "503" ] && [ "$STAGE" = "upstream_http" ] && [ "$UPSTREAM_STATUS" = "429"' in workflow
    assert "upstream-http-403" in workflow
    assert "upstream-rate-limited-429" in workflow
    # Diagnostic header/stage extraction must not trip `bash -e` + pipefail
    # when a response carries no X-Pixiv-* headers to grep for.
    assert "cut -d: -f2- | tr -d '\\r ' || true" in workflow
