"""Browser pixiv Encyclopedia client for published Pages #pixiv — no live network.

This PR retires the old browser-side Search/fetch of ``dic.pixiv.net`` (which
depended on a CORS allowance that site never grants) in favor of the
iPhone-validated Open -> Source -> Extract workflow: the panel generates and
validates the real ``/a/{title}``, ``/history/{title}``, and
``/history/{title}/{revision_id}/source`` URLs so a visitor can open them,
then normalizes source text pasted back from Pixiv's own 原文表示 (view
source) page into a stable structured extract. Everything below runs
against pasted text only; ``pages_pixiv.js`` makes no ``fetch()`` call.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from mcp_toolcall_lab.stub_front import PAGES_PIXIV_JS_SOURCE

_PAGES_PIXIV_CONTRACT = r"""
const pixiv = require(process.argv[1]);
const assert = require("assert");

// -- URL generation --------------------------------------------------
assert.strictEqual(pixiv.buildArticleUrl("Bleach"), "https://dic.pixiv.net/a/Bleach");
assert.strictEqual(
  pixiv.buildArticleUrl("ブリーチ"),
  "https://dic.pixiv.net/a/" + encodeURIComponent("ブリーチ")
);
assert.strictEqual(pixiv.buildArticleUrl(""), "");
assert.strictEqual(pixiv.buildArticleUrl("  "), "");

assert.strictEqual(pixiv.buildHistoryUrl("BLEACH"), "https://dic.pixiv.net/history/BLEACH");
assert.strictEqual(pixiv.buildHistoryUrl(""), "");

assert.strictEqual(
  pixiv.buildSourceUrl("BLEACH", "8852559"),
  "https://dic.pixiv.net/history/BLEACH/8852559/source"
);
assert.strictEqual(pixiv.buildSourceUrl("BLEACH", "not-a-number"), "");
assert.strictEqual(pixiv.buildSourceUrl("", "8852559"), "");

// -- URL recognition/validation ---------------------------------------
const validUrl = pixiv.parseSourceUrl("https://dic.pixiv.net/history/BLEACH/8852559/source");
assert.strictEqual(validUrl.ok, true);
assert.strictEqual(validUrl.title, "BLEACH");
assert.strictEqual(validUrl.revisionId, "8852559");

// Unicode title, raw (not percent-encoded) in the URL string.
const unicodeUrl = pixiv.parseSourceUrl(
  "https://dic.pixiv.net/history/ブリーチ/8852559/source"
);
assert.strictEqual(unicodeUrl.ok, true);
assert.strictEqual(unicodeUrl.title, "ブリーチ");

// Percent-encoded (URL-encoded) title also recognized.
const encodedUrl = pixiv.parseSourceUrl(
  "https://dic.pixiv.net/history/" + encodeURIComponent("ブリーチ") + "/8852559/source"
);
assert.strictEqual(encodedUrl.ok, true);
assert.strictEqual(encodedUrl.title, "ブリーチ");

// Non-numeric revision id rejected.
const badRevision = pixiv.parseSourceUrl("https://dic.pixiv.net/history/BLEACH/latest/source");
assert.strictEqual(badRevision.ok, false);
assert.strictEqual(badRevision.error, pixiv.INVALID_SOURCE_URL);

// Non-pixiv host rejected -- never treated as a trusted pixiv URL.
const wrongHost = pixiv.parseSourceUrl(
  "https://evil.example/history/BLEACH/8852559/source"
);
assert.strictEqual(wrongHost.ok, false);

// Plain article/history URLs (no /source) are not revision-source URLs.
assert.strictEqual(pixiv.parseSourceUrl("https://dic.pixiv.net/a/BLEACH").ok, false);
assert.strictEqual(pixiv.parseSourceUrl("https://dic.pixiv.net/history/BLEACH").ok, false);
assert.strictEqual(pixiv.parseSourceUrl("").ok, false);
assert.strictEqual(pixiv.parseSourceUrl("not a url").ok, false);

// -- Extract: HTML-shaped pasted source --------------------------------
const htmlExtract = pixiv.extractSource({
  sourceText:
    "<h1>テスト記事（てすときじ）</h1><p>これは概要本文。</p>" +
    "<h2>来歴</h2><p>1999年に活動を開始した。</p>",
});
assert.strictEqual(htmlExtract.ok, true);
assert.strictEqual(htmlExtract.title, "テスト記事");
assert.strictEqual(htmlExtract.reading, "てすときじ");
assert.ok(htmlExtract.overview.includes("これは概要本文。"));
assert.deepStrictEqual(htmlExtract.headings, [
  { level: 1, heading: "テスト記事（てすときじ）" },
  { level: 2, heading: "来歴" },
]);
assert.ok(htmlExtract.body.includes("1999年に活動を開始した。"));

// -- Extract: plain pasted text (no HTML heading markers available) ---
const plainExtract = pixiv.extractSource({
  title: "BLEACH",
  sourceText: "BLEACH\nA shinigami story.\nSecond paragraph.",
});
assert.strictEqual(plainExtract.ok, true);
assert.strictEqual(plainExtract.title, "BLEACH");
assert.deepStrictEqual(plainExtract.headings, []);
assert.ok(plainExtract.body.includes("A shinigami story."));

// -- Extract: missing/empty input never fakes success ------------------
assert.strictEqual(pixiv.extractSource({ sourceText: "" }).ok, false);
assert.strictEqual(pixiv.extractSource({ sourceText: "" }).error, pixiv.MISSING_SOURCE);
assert.strictEqual(pixiv.extractSource({ sourceText: "<p></p>" }).ok, false);

console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_pixiv_js_open_source_extract_contracts() -> None:
    result = subprocess.run(
        ["node", "-e", _PAGES_PIXIV_CONTRACT, str(PAGES_PIXIV_JS_SOURCE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_pixiv_js_is_valid_javascript() -> None:
    result = subprocess.run(
        ["node", "--check", str(PAGES_PIXIV_JS_SOURCE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_pages_pixiv_js_search_is_retired() -> None:
    """Regression: this is a retire-Search PR, not a fix-Search PR.

    The panel must no longer call ``fetch()`` against dic.pixiv.net, and the
    old Search-button contract (``loadArticle`` / a "Search" affordance /
    CORS fetch attempt) must not reappear.
    """
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert "fetch(" not in source
    assert "loadArticle" not in source
    assert '"cors"' not in source
    assert "mode:" not in source
    assert "Search" not in source


def test_pages_pixiv_js_never_claims_localhost_is_reachable() -> None:
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert "MCP execution is local-only" not in source
    assert "Local result URL" not in source
    assert "127.0.0.1:8765" not in source
    assert "dic.pixiv.net" in source
