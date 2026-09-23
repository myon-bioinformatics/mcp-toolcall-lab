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
    assert workflow.count('"src/mcp_toolcall_lab/pixiv_dictionary_tool.py"') == 2
