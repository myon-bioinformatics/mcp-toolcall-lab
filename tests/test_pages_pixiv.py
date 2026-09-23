"""Browser pixiv Encyclopedia client for published Pages #pixiv — no live network.

Regression coverage for the Blocking fix in PR #58: submitting the public
#pixiv panel must attempt a real fetch of the actual upstream
(https://dic.pixiv.net/a/{title}) and, on failure, say so honestly instead of
pointing the user at http://127.0.0.1:8765 as if that were reachable from
GitHub Pages.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from mcp_toolcall_lab.stub_front import PAGES_PIXIV_JS_SOURCE

_PAGES_PIXIV_CONTRACT = r"""
const pixiv = require(process.argv[1]);
const assert = require("assert");

assert.strictEqual(pixiv.buildArticleUrl("Bleach"), "https://dic.pixiv.net/a/Bleach");
assert.strictEqual(
  pixiv.buildArticleUrl("ブリーチ"),
  "https://dic.pixiv.net/a/" + encodeURIComponent("ブリーチ")
);
assert.strictEqual(pixiv.buildArticleUrl(""), "");
assert.strictEqual(pixiv.buildArticleUrl("  "), "");

const stripped = pixiv.stripHtml(
  "<html><head><style>.x{}</style></head><body>" +
    "<h1>Bleach</h1><p>A shinigami story.</p>" +
    "<script>evil()</script>" +
    "<p>Second&nbsp;paragraph &amp; more</p>" +
    "</body></html>"
);
assert.ok(stripped.includes("Bleach"));
assert.ok(stripped.includes("A shinigami story."));
assert.ok(stripped.includes("Second paragraph & more"));
assert.ok(!stripped.includes("evil()"));
assert.ok(!stripped.includes("<"));
assert.strictEqual(pixiv.stripHtml(""), "");
assert.strictEqual(pixiv.stripHtml("<p></p><p>   </p>"), "");

pixiv.loadArticle("", null).then((empty) => {
  assert.strictEqual(empty.ok, false);
  assert.strictEqual(empty.error, pixiv.MISSING_TITLE);

  let fetchCalls = 0;
  const fakeFetch = (url, init) => {
    fetchCalls += 1;
    assert.strictEqual(url, "https://dic.pixiv.net/a/Bleach");
    assert.strictEqual(init.mode, "cors");
    return Promise.resolve({ ok: true, text: () => Promise.resolve("<h1>Bleach</h1><p>body text</p>") });
  };
  return pixiv.loadArticle("Bleach", fakeFetch).then((result) => {
    assert.strictEqual(fetchCalls, 1);
    assert.strictEqual(result.ok, true);
    assert.strictEqual(result.url, "https://dic.pixiv.net/a/Bleach");
    assert.ok(result.text.includes("Bleach"));
    assert.ok(result.text.includes("body text"));

    // A real network/CORS failure (what a browser throws when a static host
    // like Pages is blocked from calling a non-CORS-enabled origin) must
    // produce an honest error -- never a fake success, never a claim that
    // 127.0.0.1 is reachable from here.
    return pixiv.loadArticle("Bleach", () => Promise.reject(new TypeError("Failed to fetch")));
  }).then((corsErr) => {
    assert.strictEqual(corsErr.ok, false);
    assert.strictEqual(corsErr.staticHost, true);
    assert.ok(corsErr.error.includes("GitHub Pages is a static host"));
    assert.ok(corsErr.error.includes("Failed to fetch"));
    assert.ok(!corsErr.error.includes("127.0.0.1"));

    return pixiv.loadArticle("Bleach", () => Promise.resolve({ ok: false, status: 403 }));
  }).then((httpErr) => {
    assert.strictEqual(httpErr.ok, false);
    assert.ok(httpErr.error.includes("HTTP 403"));

    return pixiv.loadArticle("Bleach", () => Promise.resolve({ ok: true, text: () => Promise.resolve("") }));
  }).then((emptyBody) => {
    assert.strictEqual(emptyBody.ok, false);
    assert.ok(emptyBody.error.includes("no readable text"));
    console.log("ok");
  });
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_pixiv_js_fetch_and_error_contracts() -> None:
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


def test_pages_pixiv_js_never_claims_localhost_is_reachable() -> None:
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert "MCP execution is local-only" not in source
    assert "Local result URL" not in source
    assert "127.0.0.1:8765" not in source
    assert "dic.pixiv.net" in source
