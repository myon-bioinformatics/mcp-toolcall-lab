"""Browser MediaWiki client for published Pages #wiki — no live network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_toolcall_lab.stub_front import (
    PAGES_WIKI_BROWSER_NOTE,
    PAGES_WIKI_JS_NAME,
    PAGES_WIKI_JS_SOURCE,
    write_pages,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "wikipedia" / "yokohama_extract.json"

_PAGES_WIKI_CONTRACT = r"""
const wiki = require(process.argv[1]);
const fixture = require(process.argv[2]);
const assert = require("assert");

const extract = fixture.query.pages["3436090"].extract;

const sections = wiki.parseWikiSections(extract);
assert.deepStrictEqual(
  sections.map((s) => s.title),
  ["Geography", "History", "Climate", "See also"]
);
assert.strictEqual(sections[0].level, 2);
assert.strictEqual(sections[2].level, 3);
assert.ok(sections[0].body.includes("Kanagawa Prefecture"));
assert.ok(!sections[0].body.includes("=== Climate ==="));
assert.ok(sections[1].body.includes("=== Climate ==="));
assert.ok(sections[1].body.includes("Mild in this fixture"));
assert.ok(sections[2].body.includes("Mild in this fixture"));
assert.ok(!sections[2].body.includes("== See also =="));

assert.strictEqual(wiki.headingLabel(2, "あらすじ"), "## あらすじ");
assert.strictEqual(wiki.headingLabel(3, "死神代行篇"), "### 死神代行篇");

const bleach = [
  "lead",
  "",
  "== あらすじ ==",
  "",
  "=== 死神代行篇 ===",
  "arc1",
  "=== 尸魂界篇 ===",
  "arc2",
  "== 登場人物 ==",
  "people",
].join("\n");
const bleachSections = wiki.parseWikiSections(bleach);
assert.strictEqual(bleachSections[0].title, "あらすじ");
assert.strictEqual(bleachSections[0].level, 2);
assert.ok(bleachSections[0].body.includes("=== 死神代行篇 ==="));
assert.ok(bleachSections[0].body.includes("arc1"));
assert.ok(bleachSections[0].body.includes("=== 尸魂界篇 ==="));
assert.ok(bleachSections[0].body.includes("arc2"));
assert.ok(!bleachSections[0].body.includes("== 登場人物 =="));
assert.strictEqual(wiki.sectionView(bleach, "あらすじ"), bleachSections[0].body);

assert.ok(wiki.sectionView(extract, "").includes("== Geography =="));
assert.strictEqual(wiki.sectionView(extract, "Geography"), sections[0].body);
assert.strictEqual(wiki.sectionView(extract, "climate"), sections[2].body);
assert.ok(wiki.sectionView(extract, "no-such-heading").includes("== Geography =="));

assert.strictEqual(
  wiki.escapeHtml('Lead <script>alert("xss")</script>'),
  "Lead &lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;"
);

const url = wiki.buildApiUrl("Yokohama", "en");
assert.ok(url.startsWith("https://en.wikipedia.org/w/api.php?"));
assert.ok(url.includes("action=query"));
assert.ok(url.includes("prop=extracts"));
assert.ok(url.includes("explaintext=1"));
assert.ok(url.includes("exsectionformat=wiki"));
assert.ok(url.includes("redirects=1"));
assert.ok(url.includes("format=json"));
assert.ok(url.includes("origin=*"));
assert.ok(url.includes("titles=Yokohama"));
assert.strictEqual(wiki.buildApiUrl("", "en"), "");
assert.strictEqual(wiki.buildApiUrl("Yokohama", "en.evil"), "");
assert.ok(wiki.buildApiUrl("Yokohama", "JA").startsWith("https://ja.wikipedia.org/"));

const parsed = wiki.parseExtractPayload(fixture, "Yokohama, Japan", "en");
assert.strictEqual(parsed.ok, true);
assert.strictEqual(parsed.canonical_title, "Yokohama");
assert.ok(parsed.extract.includes("== Geography =="));
assert.strictEqual(parsed.sections.length, 4);

const missing = wiki.parseExtractPayload(
  { query: { pages: { "-1": { title: "Nope", missing: "" } } } },
  "Nope",
  "en"
);
assert.strictEqual(missing.ok, false);
assert.ok(missing.error.includes("no en.wikipedia.org article named 'Nope'"));

const badJson = wiki.parseExtractPayload(null, "Yokohama", "en");
assert.strictEqual(badJson.ok, false);

assert.deepStrictEqual(wiki.parseWikiHash("#wiki?title=Yokohama&lang=ja"), {
  title: "Yokohama",
  lang: "ja",
  heading: "",
});
assert.deepStrictEqual(wiki.parseWikiHash("#wiki?title=Yokohama&lang=en&heading=Geography"), {
  title: "Yokohama",
  lang: "en",
  heading: "Geography",
});
assert.strictEqual(wiki.wikiHash({ title: "Yokohama", lang: "ja" }), "#wiki?title=Yokohama&lang=ja");

const view = wiki.viewState(parsed, "Geography");
assert.strictEqual(view.canonical_title, "Yokohama");
assert.deepStrictEqual(view.headings, [
  { title: "Geography", level: 2 },
  { title: "History", level: 2 },
  { title: "Climate", level: 3 },
  { title: "See also", level: 2 },
]);
assert.ok(view.body.includes("Kanagawa Prefecture"));
assert.ok(!view.body.includes("== History =="));
const historyView = wiki.viewState(parsed, "History");
assert.ok(historyView.body.includes("=== Climate ==="));
assert.ok(historyView.body.includes("Mild in this fixture"));
assert.ok(!historyView.body.includes("== See also =="));

wiki.loadArticle("", "en").then((empty) => {
  assert.strictEqual(empty.ok, false);
  assert.strictEqual(empty.error, wiki.MISSING_TITLE);

  let fetchCalls = 0;
  const fakeFetch = (url) => {
    fetchCalls += 1;
    assert.ok(String(url).includes("origin=*"));
    assert.ok(String(url).includes("titles=Yokohama"));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(fixture) });
  };
  const session = wiki.createSession(fakeFetch);
  return session.load("Yokohama", "en").then((article) => {
    assert.strictEqual(article.ok, true);
    assert.strictEqual(session.fetches(), 1);
    const geo = session.selectHeading("Geography");
    assert.strictEqual(session.fetches(), 1);
    assert.ok(geo.body.includes("Kanagawa Prefecture"));
    const full = session.selectHeading("");
    assert.strictEqual(session.fetches(), 1);
    assert.ok(full.body.includes("== Geography =="));
    return wiki.loadArticle("Yokohama", "en", () => Promise.reject(new Error("Failed to fetch")));
  }).then((netErr) => {
    assert.strictEqual(netErr.ok, false);
    assert.ok(netErr.error.includes("could not fetch"));
    assert.ok(netErr.error.includes("Failed to fetch"));
    return wiki.loadArticle("Yokohama", "en", () => Promise.resolve({ ok: false, status: 503 }));
  }).then((httpErr) => {
    assert.strictEqual(httpErr.ok, false);
    assert.ok(httpErr.error.includes("HTTP 503"));
    console.log("ok");
  });
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_wiki_js_heading_fetch_and_view_contracts() -> None:
    result = subprocess.run(
        ["node", "-e", _PAGES_WIKI_CONTRACT, str(PAGES_WIKI_JS_SOURCE), str(FIXTURE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_wiki_js_is_valid_javascript() -> None:
    result = subprocess.run(
        ["node", "--check", str(PAGES_WIKI_JS_SOURCE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_write_pages_includes_mediawiki_ui_mount(tmp_path: Path) -> None:
    out = write_pages(tmp_path / "site").parent
    html = (out / "index.html").read_text(encoding="utf-8")
    assert PAGES_WIKI_BROWSER_NOTE in html
    assert 'id="pages-wiki-app"' in html
    assert 'data-testid="pages-wiki-app"' in html
    assert 'data-testid="pages-wiki-form"' in html
    assert 'data-testid="pages-wiki-title"' in html
    assert 'value="Yokohama"' in html
    assert 'data-testid="pages-wiki-lang"' in html
    assert 'option value="en" selected' in html
    assert 'option value="ja"' in html
    assert 'data-testid="pages-wiki-fetch"' in html
    assert 'data-testid="pages-wiki-heading"' in html
    assert "(full extract)" in html
    assert 'data-testid="pages-wiki-extract"' in html
    assert f'src="{PAGES_WIKI_JS_NAME}?v=' in html
    assert (out / PAGES_WIKI_JS_NAME).is_file()
    assert 'href="#wiki"' in html
    assert 'data-pages-view="wiki"' in html
    assert 'action="/wiki"' not in html
    assert "Try it (static, no MCP)" not in html
    assert "mcpToolcallLabStubDemo" not in html
    assert "Corpus headings" not in html


def test_yokohama_fixture_is_mediawiki_extract_shape() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = payload["query"]["pages"]["3436090"]
    assert page["title"] == "Yokohama"
    assert "== Geography ==" in page["extract"]
    assert "missing" not in page
