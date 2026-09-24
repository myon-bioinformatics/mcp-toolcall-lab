"""Browser pixiv Encyclopedia client for published Pages #pixiv — no live network.

This PR retires the old browser-side Search/fetch of ``dic.pixiv.net`` (which
depended on a CORS allowance that site never grants) in favor of the
Open -> Source -> Extract workflow: the panel generates and validates the
real ``/a/{title}``, ``/history/{title}``, and
``/history/{title}/{revision_id}/source`` URLs so a visitor can open them,
then normalizes source text pasted back from Pixiv's own 原文表示 (view
source) page into a stable structured extract. Everything below runs
against pasted text only; ``pages_pixiv.js`` makes no ``fetch()`` call.

The URL contract table (valid/invalid revision-source URLs, ``buildSourceUrl``
round trips) lives in ``fixtures/pixiv_dictionary/history_source_urls.json``
as data rather than inline JS so the contract can be read/extended without
touching the test script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_toolcall_lab.stub_front import PAGES_PIXIV_JS_SOURCE

ROOT = Path(__file__).resolve().parents[1]
URL_FIXTURE = ROOT / "fixtures" / "pixiv_dictionary" / "history_source_urls.json"
NORMALIZATION_FIXTURE = ROOT / "fixtures" / "pixiv_dictionary" / "markup_normalization_cases.json"

_PAGES_PIXIV_CONTRACT = r"""
const fs = require("fs");
const pixiv = require(process.argv[1]);
const assert = require("assert");
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const normalizationFixtures = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

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

// -- buildSourceUrl: fixture-driven valid/invalid/round-trip cases ----
// Percent-encoding (e.g. title "A/B" -> "A%2FB") is computed here via
// encodeURIComponent, not hand-computed in the fixture, so the fixture file
// never hardcodes a percent-encoding byte sequence that could silently drift
// from what encodeURIComponent actually produces.
fixtures.build_source_url.forEach(function (c) {
  var expected = c.expectedEncodeTitle
    ? "https://dic.pixiv.net/history/" + encodeURIComponent(c.title) + "/" + c.revisionId + "/source"
    : c.expected;
  assert.strictEqual(
    pixiv.buildSourceUrl(c.title, c.revisionId),
    expected,
    (c.reason || "buildSourceUrl") + ": " + JSON.stringify(c)
  );
});

// -- parseSourceUrl: fixture-driven valid cases (JP/raw-Unicode/percent- --
// encoded titles, title-with-slash -> %2F, trailing slash, query, fragment,
// and the 15-digit revision-id cap) ------------------------------------
fixtures.valid_source_urls.forEach(function (c) {
  var url = c.encodeTitleInUrl
    ? "https://dic.pixiv.net/history/" + encodeURIComponent(c.rawTitle) + "/" + c.revisionId + "/source"
    : c.url;
  var result = pixiv.parseSourceUrl(url);
  assert.strictEqual(result.ok, true, c.label + ": " + url);
  assert.strictEqual(result.title, c.title, c.label);
  assert.strictEqual(result.revisionId, c.revisionId, c.label);
});

// -- parseSourceUrl: fixture-driven invalid cases (wrong host, host-suffix -
// trick, http instead of https, zero/leading-zero/over-cap revision id, --
// non-numeric revision id, non-source URLs, empty/garbage input) --------
fixtures.invalid_source_urls.forEach(function (c) {
  var result = pixiv.parseSourceUrl(c.url);
  assert.strictEqual(result.ok, false, c.label + ": " + c.url);
  assert.strictEqual(result.error, pixiv.INVALID_SOURCE_URL, c.label);
});

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


// -- Shared fixture pins browser/Python normalization parity ---------------
normalizationFixtures.cases.forEach(function (c) {
  var actual = pixiv.pixivToMarkdown(c.input);
  assert.strictEqual(actual, c.expected, c.name);
  assert.strictEqual(pixiv.pixivToMarkdown(actual), actual, c.name + " idempotence");
});

// -- Pages mirrors the #61/#62 conservative Pixiv normalization ----------
const observedShape = [
  "*【INFORMATION】／作品情報",
  "-[[黒崎一護]]",
  "[pixivimage:61456650]",
  "NEXT▶︎[[獄頤鳴鳴篇]]",
  "[[死神>死神(BLEACH)]]",
].join("\n");
const normalizedShape = pixiv.pixivToMarkdown(observedShape);
assert.ok(normalizedShape.includes("# 【INFORMATION】／作品情報"));
assert.ok(normalizedShape.includes("- [黒崎一護](黒崎一護)"));
assert.ok(normalizedShape.includes("![pixiv image 61456650](pixivimage:61456650)"));
assert.ok(normalizedShape.includes("NEXT ▶︎ [獄頤鳴鳴篇](獄頤鳴鳴篇)"));
assert.ok(normalizedShape.includes("[死神](死神%28BLEACH%29)"));
assert.strictEqual(pixiv.pixivToMarkdown(normalizedShape), normalizedShape);

const normalizedExtract = pixiv.extractSource({
  title: "synthetic",
  sourceText: observedShape,
});
assert.strictEqual(normalizedExtract.ok, true);
assert.deepStrictEqual(normalizedExtract.headings, [
  { level: 1, heading: "【INFORMATION】／作品情報" },
]);
assert.ok(normalizedExtract.body.includes("- [黒崎一護](黒崎一護)"));
assert.ok(normalizedExtract.body.includes("![pixiv image 61456650](pixivimage:61456650)"));

const autoTitleExtract = pixiv.extractSource({
  sourceText: "*【INFORMATION】／作品情報\n本文",
});
assert.strictEqual(autoTitleExtract.ok, true);
assert.strictEqual(autoTitleExtract.title, "【INFORMATION】／作品情報");
assert.deepStrictEqual(autoTitleExtract.headings, [
  { level: 1, heading: "【INFORMATION】／作品情報" },
]);

// -- Extract: missing/empty input never fakes success ------------------
assert.strictEqual(pixiv.extractSource({ sourceText: "" }).ok, false);
assert.strictEqual(pixiv.extractSource({ sourceText: "" }).error, pixiv.MISSING_SOURCE);
assert.strictEqual(pixiv.extractSource({ sourceText: "<p></p>" }).ok, false);

// -- Extract: paste line-break normalization ---------------------------
// iOS/Safari clipboard paste can carry CRLF, a lone CR, or the Unicode
// line/paragraph separators (U+2028/U+2029) instead of LF. Built from code
// points, not literal source characters: U+2028/U+2029 are themselves
// ECMAScript line terminators, so embedding them literally in this test
// script's source would risk the exact bug being tested for.
const LINE_SEPARATOR = String.fromCharCode(0x2028);
const PARAGRAPH_SEPARATOR = String.fromCharCode(0x2029);
const NBSP = String.fromCharCode(0x00a0);
const FULLWIDTH_SPACE = String.fromCharCode(0x3000);

[
  { label: "CRLF", text: "<h1>Title</h1><p>Line one.\r\nLine two.</p>" },
  { label: "lone CR", text: "<h1>Title</h1><p>Line one.\rLine two.</p>" },
  { label: "U+2028 line separator", text: "<h1>Title</h1><p>Line one." + LINE_SEPARATOR + "Line two.</p>" },
  { label: "U+2029 paragraph separator", text: "<h1>Title</h1><p>Line one." + PARAGRAPH_SEPARATOR + "Line two.</p>" },
].forEach(function (c) {
  var result = pixiv.extractSource({ sourceText: c.text });
  assert.strictEqual(result.ok, true, c.label);
  assert.ok(result.body.indexOf("Line one.") !== -1, c.label);
  assert.ok(result.body.indexOf("Line two.") !== -1, c.label);
  assert.strictEqual(result.body.indexOf("\r"), -1, c.label);
  assert.strictEqual(result.body.indexOf(LINE_SEPARATOR), -1, c.label);
  assert.strictEqual(result.body.indexOf(PARAGRAPH_SEPARATOR), -1, c.label);
});

// -- Extract: NBSP / full-width space fold to a normal space ------------
const nbspExtract = pixiv.extractSource({
  sourceText: "<h1>Title</h1><p>Word" + NBSP + "gap." + FULLWIDTH_SPACE + "Another.</p>",
});
assert.strictEqual(nbspExtract.ok, true);
assert.strictEqual(nbspExtract.body.indexOf(NBSP), -1);
assert.strictEqual(nbspExtract.body.indexOf(FULLWIDTH_SPACE), -1);
assert.ok(nbspExtract.body.indexOf("Word gap. Another.") !== -1);

// -- Extract: trailing whitespace (including NBSP/full-width space) never
// survives into the extracted body --------------------------------------
const trailingWhitespaceExtract = pixiv.extractSource({
  sourceText: "<h1>Title</h1><p>Trailing space line.   " + NBSP + FULLWIDTH_SPACE + "  </p>",
});
assert.strictEqual(trailingWhitespaceExtract.ok, true);
const trailingLines = trailingWhitespaceExtract.body.split("\n");
assert.strictEqual(trailingLines[trailingLines.length - 1], "Trailing space line.");

// -- Extract: input-size budget, checked before decode/trim -------------
const atLimitText = "T".repeat(pixiv.MAX_SOURCE_LENGTH);
assert.strictEqual(pixiv.extractSource({ sourceText: atLimitText }).ok, true);

const overLimitText = "T".repeat(pixiv.MAX_SOURCE_LENGTH + 1);
const overLimitResult = pixiv.extractSource({ sourceText: overLimitText });
assert.strictEqual(overLimitResult.ok, false);
assert.strictEqual(overLimitResult.error, pixiv.SOURCE_TOO_LARGE);

// Padding that would vanish after trim() must still be rejected -- the
// budget check runs on the raw untrimmed/undecoded paste, not after it.
const overLimitByTrimmablePadding = "T".repeat(pixiv.MAX_SOURCE_LENGTH) + "   ";
const overLimitByPaddingResult = pixiv.extractSource({ sourceText: overLimitByTrimmablePadding });
assert.strictEqual(overLimitByPaddingResult.ok, false);
assert.strictEqual(overLimitByPaddingResult.error, pixiv.SOURCE_TOO_LARGE);

console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_pixiv_js_open_source_extract_contracts() -> None:
    result = subprocess.run(
        [
            "node",
            "-e",
            _PAGES_PIXIV_CONTRACT,
            str(PAGES_PIXIV_JS_SOURCE),
            str(URL_FIXTURE),
            str(NORMALIZATION_FIXTURE),
        ],
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


def test_pages_pixiv_js_never_embeds_line_terminator_chars_in_source() -> None:
    """U+2028/U+2029 are ECMAScript source line terminators.

    ``normalizeLineBreaks``/``normalizeSpaces`` must build those code points
    at runtime (``String.fromCharCode``), never write the literal characters
    into a regex/string literal in the shipped source -- a stray literal
    U+2028/U+2029 inside a regex literal is a syntax error, and even inside a
    string literal it is an easy-to-reintroduce footgun this file avoids.

    Compared via ``chr(0x2028)``/``chr(0x2029)`` rather than a literal
    character in this test's own source, so this file does not itself carry
    an invisible line/paragraph separator byte for a diff viewer to mangle.
    """
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert chr(0x2028) not in source
    assert chr(0x2029) not in source


def test_history_source_url_fixture_is_synthetic_only() -> None:
    """Non-blocking ask: URL fixtures stay synthetic, no article body text.

    The one real value used anywhere (``BLEACH`` / ``8852559``) is a title +
    revision id pulled from the manually-verified URL shape in
    docs/stub_pages.md, not dumped article content.
    """
    fixtures = json.loads(URL_FIXTURE.read_text(encoding="utf-8"))
    assert "_provenance" in fixtures
    dumped = json.dumps(fixtures, ensure_ascii=False)
    # No pixiv article prose -- only URLs, titles, and revision ids.
    assert "概要" not in dumped
    assert "来歴" not in dumped
