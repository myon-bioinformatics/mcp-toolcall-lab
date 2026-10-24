"""Browser Wikipedia MCP client for published Pages #wiki — no live network."""

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
    PAGES_WIKI_MCP_URL_ENV,
    WIKI_EXTRACT_NOTE,
    write_pages,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "wikipedia" / "exampleville_extract.json"

_PAGES_WIKI_CONTRACT = r"""
const wiki = require(process.argv[1]);
const fixture = require(process.argv[2]);
const assert = require("assert");

const MCP = "https://example.deno.dev/mcp";
const extract = fixture.query.pages["9000001"].extract;

const sseParsed = wiki.extractSseData(
  'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
);
assert.strictEqual(sseParsed.jsonrpc, "2.0");
assert.strictEqual(sseParsed.id, 1);
assert.strictEqual(sseParsed.result.ok, true);
assert.ok(!("event" in sseParsed));

const sections = wiki.parseWikiSections(extract);
assert.deepStrictEqual(
  sections.map((s) => s.title),
  ["Geography", "History", "Climate", "See also"]
);
assert.strictEqual(sections[0].level, 2);
assert.strictEqual(sections[2].level, 3);
assert.ok(sections[0].body.includes("Example Region"));
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

assert.strictEqual(wiki.normalizeLang("JA"), "ja");
assert.strictEqual(wiki.normalizeLang("en.evil"), "");
assert.strictEqual(wiki.normalizeLang(""), wiki.DEFAULT_LANG);

const parsed = wiki.parseExtractPayload(fixture, "Exampleville, Demo", "en");
assert.strictEqual(parsed.ok, true);
assert.strictEqual(parsed.canonical_title, "Exampleville");
assert.ok(parsed.extract.includes("== Geography =="));
assert.strictEqual(parsed.sections.length, 4);

const missing = wiki.parseExtractPayload(
  { query: { pages: { "-1": { title: "Nope", missing: "" } } } },
  "Nope",
  "en"
);
assert.strictEqual(missing.ok, false);
assert.ok(missing.error.includes("no en.wikipedia.org article named 'Nope'"));

const badJson = wiki.parseExtractPayload(null, "Exampleville", "en");
assert.strictEqual(badJson.ok, false);

assert.deepStrictEqual(wiki.parseWikiHash("#wiki?title=Exampleville&lang=ja"), {
  title: "Exampleville",
  lang: "ja",
  heading: "",
});
assert.deepStrictEqual(wiki.parseWikiHash("#wiki?title=Exampleville&lang=en&heading=Geography"), {
  title: "Exampleville",
  lang: "en",
  heading: "Geography",
});
assert.strictEqual(wiki.wikiHash({ title: "Exampleville", lang: "ja" }), "#wiki?title=Exampleville&lang=ja");

const view = wiki.viewState(parsed, "Geography");
assert.strictEqual(view.canonical_title, "Exampleville");
assert.deepStrictEqual(view.headings, [
  { title: "Geography", level: 2 },
  { title: "History", level: 2 },
  { title: "Climate", level: 3 },
  { title: "See also", level: 2 },
]);
assert.ok(view.body.includes("Example Region"));
assert.ok(!view.body.includes("== History =="));
const historyView = wiki.viewState(parsed, "History");
assert.ok(historyView.body.includes("=== Climate ==="));
assert.ok(historyView.body.includes("Mild in this fixture"));
assert.ok(!historyView.body.includes("== See also =="));

const wrapped = wiki.unwrapArticle(
  {
    jsonrpc: "2.0",
    id: 9,
    result: {
      content: [{ type: "text", text: JSON.stringify({ result: {
        canonical_title: "Exampleville",
        lang: "en",
        extract: parsed.extract,
      }}) }],
      structuredContent: { result: {
        canonical_title: "Exampleville",
        lang: "en",
        extract: parsed.extract,
      }},
      isError: false,
    },
  },
  "Exampleville",
  "en"
);
assert.strictEqual(wrapped.ok, true);
assert.strictEqual(wrapped.canonical_title, "Exampleville");

function headers(map) {
  return {
    get: (name) => {
      const key = String(name).toLowerCase();
      if (map[name] != null) return map[name];
      if (map[key] != null) return map[key];
      return null;
    },
  };
}

function sse(obj) {
  return {
    ok: true,
    status: 200,
    headers: headers({ "mcp-session-id": "sess_labtoken.1.mac" }),
    text: () => Promise.resolve("event: message\ndata: " + JSON.stringify(obj) + "\n\n"),
  };
}

function accepted() {
  return {
    ok: true,
    status: 202,
    headers: headers({ "mcp-session-id": "sess_labtoken.1.mac" }),
    text: () => Promise.resolve(""),
  };
}

function articleRpc() {
  const article = {
    canonical_title: parsed.canonical_title,
    lang: "en",
    extract: parsed.extract,
    headings: parsed.sections.map((s) => ({ heading: s.title, level: s.level })),
  };
  return {
    jsonrpc: "2.0",
    id: 3,
    result: {
      content: [{ type: "text", text: JSON.stringify(article) }],
      structuredContent: article,
      isError: false,
    },
  };
}

function listRpc() {
  return {
    jsonrpc: "2.0",
    id: 2,
    result: {
      tools: [{ name: "fetch_wikipedia_article" }, { name: "fetch_wikipedia_section" }],
    },
  };
}

wiki.loadArticle("", "en", null, MCP).then((empty) => {
  assert.strictEqual(empty.ok, false);
  assert.strictEqual(empty.error, wiki.MISSING_TITLE);
  return wiki.loadArticle("Exampleville", "en");
}).then((noUrl) => {
  assert.strictEqual(noUrl.ok, false);
  assert.strictEqual(noUrl.error, wiki.MISSING_MCP);

  let fetchCalls = 0;
  const fakeFetch = (url, init) => {
    fetchCalls += 1;
    assert.strictEqual(url, MCP);
    assert.strictEqual(init.method, "POST");
    assert.ok(String(init.headers.Accept).includes("text/event-stream"));
    assert.ok(!String(init.body).includes("origin=*"));
    const body = JSON.parse(init.body);
    assert.strictEqual(body.jsonrpc, "2.0");
    if (body.method === "initialize") {
      assert.strictEqual(init.headers["MCP-Protocol-Version"], undefined);
      assert.strictEqual(body.params.protocolVersion, wiki.PROTOCOL_VERSION);
      return Promise.resolve(sse({ jsonrpc: "2.0", id: 1, result: { protocolVersion: wiki.PROTOCOL_VERSION } }));
    }
    // All post-initialize requests MUST use the server-negotiated version.
    assert.strictEqual(init.headers["MCP-Protocol-Version"], wiki.PROTOCOL_VERSION);
    if (body.method === "notifications/initialized") {
      assert.ok(body.id == null);
      assert.ok(init.headers["Mcp-Session-Id"]);
      return Promise.resolve(accepted());
    }
    if (body.method === "tools/list") {
      assert.ok(init.headers["Mcp-Session-Id"]);
      return Promise.resolve(sse(listRpc()));
    }
    if (body.method === "tools/call") {
      assert.strictEqual(body.params.name, wiki.ARTICLE_TOOL);
      assert.strictEqual(body.params.arguments.title, "Exampleville");
      assert.strictEqual(body.params.arguments.lang, "en");
      return Promise.resolve(sse(articleRpc()));
    }
    throw new Error("unexpected method " + body.method);
  };
  const session = wiki.createSession(fakeFetch, MCP);
  return session.load("Exampleville", "en").then((article) => {
    assert.strictEqual(article.ok, true);
    assert.strictEqual(article.canonical_title, "Exampleville");
    assert.strictEqual(session.fetches(), 1);
    assert.deepStrictEqual(session.hops(), [
      "initialize",
      "notifications/initialized",
      "tools/list",
      "tools/call",
    ]);
    assert.strictEqual(session.sessionId(), "sess_labtoken.1.mac");
    const geo = session.selectHeading("Geography");
    assert.strictEqual(session.fetches(), 1);
    assert.deepStrictEqual(session.hops(), [
      "initialize",
      "notifications/initialized",
      "tools/list",
      "tools/call",
    ]);
    assert.ok(geo.body.includes("Example Region"));
    assert.ok(!geo.body.includes("== History =="));
    const full = session.selectHeading("");
    assert.strictEqual(session.fetches(), 1);
    assert.ok(full.body.includes("== Geography =="));
    return session.load("Exampleville", "en").then((second) => {
      assert.strictEqual(second.ok, true);
      assert.strictEqual(session.fetches(), 2);
      assert.deepStrictEqual(session.hops(), [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
        "tools/call",
      ]);
      assert.ok(fetchCalls >= 5);
      return wiki.loadArticle(
        "Exampleville",
        "en",
        () => Promise.reject(new Error("Failed to fetch")),
        MCP
      );
    });
  }).then((netErr) => {
    assert.strictEqual(netErr.ok, false);
    assert.ok(netErr.error.includes("could not fetch"));
    assert.ok(netErr.error.includes("Failed to fetch"));
    return wiki.loadArticle(
      "Exampleville",
      "en",
      () => Promise.resolve({
        ok: false,
        status: 503,
        headers: { get: () => null },
        text: () => Promise.resolve(""),
      }),
      MCP
    );
  }).then((httpErr) => {
    assert.strictEqual(httpErr.ok, false);
    assert.ok(httpErr.error.includes("HTTP 503"));
    return wiki.loadArticle("Exampleville", "en.evil", fakeFetch, MCP);
  }).then((badLang) => {
    assert.strictEqual(badLang.ok, false);
    assert.ok(badLang.error.includes("Unsupported language"));
    // An unsupported or missing negotiated protocol version must stop the
    // handshake before notifications/initialized, tools/list or tools/call.
    return Promise.all(["2024-11-05", null].map((version) => {
      let calls = 0;
      return wiki.loadArticle("Exampleville", "en", (_url, init) => {
        calls += 1;
        assert.strictEqual(JSON.parse(init.body).method, "initialize");
        assert.strictEqual(init.headers["MCP-Protocol-Version"], undefined);
        return Promise.resolve(sse({
          jsonrpc: "2.0", id: 1, result: version ? { protocolVersion: version } : {},
        }));
      }, MCP).then((failed) => {
        assert.strictEqual(failed.ok, false);
        assert.ok(failed.error.includes("unsupported MCP protocol version"));
        assert.strictEqual(calls, 1);
      });
    }));
  }).then(() => {
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


def test_write_pages_includes_wikipedia_mcp_ui_mount(tmp_path: Path) -> None:
    out = write_pages(tmp_path / "site").parent
    html = (out / "index.html").read_text(encoding="utf-8")
    assert PAGES_WIKI_BROWSER_NOTE in html
    assert WIKI_EXTRACT_NOTE in html
    assert 'id="pages-wiki-app"' in html
    assert 'data-testid="pages-wiki-app"' in html
    assert 'data-mcp-url="' in html
    assert 'data-testid="pages-wiki-mcp-endpoint"' in html
    assert PAGES_WIKI_MCP_URL_ENV in html
    assert 'data-testid="pages-wiki-form"' in html
    assert 'data-testid="pages-wiki-title"' in html
    assert 'name="title" value=""' in html
    assert 'value="Exampleville"' not in html
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
    assert "w/api.php" not in html
    assert "buildApiUrl" not in (out / PAGES_WIKI_JS_NAME).read_text(encoding="utf-8")
    assert "Try it (static, no MCP)" not in html
    assert "mcpToolcallLabStubDemo" not in html
    assert "Corpus headings" not in html
    wiki = html[html.index('data-pages-view="wiki"') :]
    i_disc = wiki.index('data-testid="pages-wiki-disclaimer"')
    i_form = wiki.index('data-testid="pages-wiki-form"')
    i_heading = wiki.index('data-testid="pages-wiki-heading"')
    i_note = wiki.index('data-testid="pages-wiki-extract-note"')
    i_extract = wiki.index('data-testid="pages-wiki-extract"')
    assert i_disc < i_form < i_heading < i_note < i_extract
    assert PAGES_WIKI_BROWSER_NOTE in wiki[i_disc:i_form]
    assert WIKI_EXTRACT_NOTE in wiki[i_note:i_extract]


def test_write_pages_bakes_mcp_url_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PAGES_WIKI_MCP_URL_ENV, "https://wiki-mcp.example/mcp")
    html = write_pages(tmp_path / "site").read_text(encoding="utf-8")
    assert 'data-mcp-url="https://wiki-mcp.example/mcp"' in html
    assert "https://wiki-mcp.example/mcp" in html[html.index('data-testid="pages-wiki-mcp-endpoint"') :]


def test_exampleville_fixture_is_mediawiki_extract_shape() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = payload["query"]["pages"]["9000001"]
    assert page["title"] == "Exampleville"
    assert "== Geography ==" in page["extract"]
    assert "missing" not in page
