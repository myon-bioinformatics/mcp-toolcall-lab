"""Wikipedia in the browser and over Deno: the Pages #wiki client and the Deno Streamable HTTP MCP."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab.mcp_http import ACCEPT, extract_sse_data
from mcp_toolcall_lab.prompt_experiment import (
    MCP_INITIALIZE, MCP_INITIALIZED, MCP_TOOLS_CALL, MCP_TOOLS_LIST, hop_request, hop_response, iter_cases, iter_mcp_hops,
)
from mcp_toolcall_lab.stub_front import (
    PAGES_WIKI_BROWSER_NOTE, PAGES_WIKI_JS_NAME, PAGES_WIKI_JS_SOURCE, PAGES_WIKI_MCP_URL_ENV, WIKI_EXTRACT_NOTE, write_pages,
)
from mcp_toolcall_lab.wikipedia_mcp_catalog import CATALOG_PATH, WIKIPEDIA_TOOL_NAMES, catalog_document, load_wikipedia_specs, write_catalog
from mcp_toolcall_lab.wikipedia_tool import FIXTURE_ENV
from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server


# ═══ · pages wiki ════════════════════════════════════════════════════════════════════════════════════════
# Browser Wikipedia MCP client for published Pages #wiki — no live network.

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
  return wiki.loadArticle("Exampleville", "en", () => Promise.reject(new Error("offline")));
}).then((noUrl) => {
  assert.strictEqual(noUrl.ok, false);
  assert.ok(noUrl.error.includes("offline"));

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
    // A transient first-initialize HTTP error must not poison the client.
    let attempts = 0;
    const retryMethods = [];
    const retryFetch = (_url, init) => {
      const body = JSON.parse(init.body);
      retryMethods.push(body.method);
      if (body.method === "initialize") {
        attempts += 1;
        assert.strictEqual(init.headers["MCP-Protocol-Version"], undefined);
        if (attempts === 1) {
          return Promise.resolve({ ok: false, status: 503, headers: headers({}), text: () => Promise.resolve("") });
        }
        return Promise.resolve(sse({ jsonrpc: "2.0", id: body.id, result: { protocolVersion: wiki.PROTOCOL_VERSION } }));
      }
      assert.strictEqual(init.headers["MCP-Protocol-Version"], wiki.PROTOCOL_VERSION);
      if (body.method === "notifications/initialized") return Promise.resolve(accepted());
      if (body.method === "tools/list") return Promise.resolve(sse(listRpc()));
      if (body.method === "tools/call") return Promise.resolve(sse(articleRpc()));
      throw new Error("unexpected method " + body.method);
    };
    const retrySession = wiki.createSession(retryFetch, MCP);
    return retrySession.load("Exampleville", "en").then((failed) => {
      assert.strictEqual(failed.ok, false);
      assert.ok(failed.error.includes("HTTP 503"));
      assert.strictEqual(retrySession.sessionId(), null);
      return retrySession.load("Exampleville", "en");
    }).then((ok) => {
      assert.strictEqual(ok.ok, true);
      assert.strictEqual(attempts, 2);
      assert.deepStrictEqual(retryMethods, [
        "initialize", "initialize", "notifications/initialized", "tools/list", "tools/call",
      ]);
    });
  }).then(async () => {
    // An expired established session must be discarded before a user retry.
    let inits = 0, calls = 0;
    const recovering = wiki.createSession(async (_url, init) => {
      const body = JSON.parse(init.body);
      if (body.method === "initialize") {
        inits++;
        assert.strictEqual(init.headers["Mcp-Session-Id"], undefined);
        assert.strictEqual(init.headers["MCP-Protocol-Version"], undefined);
        return sse({jsonrpc: "2.0", id: body.id, result: {protocolVersion: wiki.PROTOCOL_VERSION}});
      }
      if (body.method === "notifications/initialized") return accepted();
      if (body.method === "tools/list") return sse(listRpc());
      if (++calls === 2) return {ok: false, status: 404, headers: headers({})};
      return sse(articleRpc());
    }, MCP);
    assert.strictEqual((await recovering.load("Exampleville", "en")).ok, true);
    const failed = await recovering.load("Exampleville", "en");
    assert.strictEqual(failed.ok, false);
    assert.ok(failed.error.includes("HTTP 404"));
    assert.strictEqual(recovering.sessionId(), null);
    assert.strictEqual((await recovering.load("Exampleville", "en")).ok, true);
    assert.strictEqual(inits, 2);
    assert.deepStrictEqual(recovering.hops(), [
      "initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call",
      "initialize", "notifications/initialized", "tools/list", "tools/call",
    ]);
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
    assert "disabled (direct browser → Wikipedia)" in html
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
    assert "buildApiUrl" in (out / PAGES_WIKI_JS_NAME).read_text(encoding="utf-8")
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



def test_stub_pages_workflow_wires_mcp_endpoint_before_publish() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    build = workflow.split("- name: Build static Pages tree", 1)[1].split(
        "- name: Capture Pages screenshots", 1
    )[0]
    assert "MCP_TOOLCALL_LAB_PAGES_MCP_URL: ${{ vars.MCP_TOOLCALL_LAB_PAGES_MCP_URL }}" in build
    assert '[[ -n "$MCP_TOOLCALL_LAB_PAGES_MCP_URL" && "$MCP_TOOLCALL_LAB_PAGES_MCP_URL" != https://* ]]' in build
    assert '"$GITHUB_EVENT_NAME" != "pull_request"' not in build
    assert "exit 1" in build
    assert "python -m mcp_toolcall_lab.stub_front pages --out _site" in build

def test_exampleville_fixture_is_mediawiki_extract_shape() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = payload["query"]["pages"]["9000001"]
    assert page["title"] == "Exampleville"
    assert "== Geography ==" in page["extract"]
    assert "missing" not in page


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_optional_mcp_direct_transport_contract() -> None:
    script = r"""
const wiki = require(process.argv[1]);
const fixture = require(process.argv[2]);
const assert = require('node:assert/strict');
(async () => {
  let calls = 0;
  const direct = wiki.createSession(async (url, init) => {
    calls++;
    assert.equal(init, undefined);
    const parsed = new URL(url);
    assert.equal(parsed.origin, 'https://en.wikipedia.org');
    assert.equal(parsed.pathname, '/w/api.php');
    assert.equal(parsed.searchParams.get('origin'), '*');
    assert.equal(parsed.searchParams.get('titles'), 'Exampleville');
    return {ok: true, json: async () => fixture};
  }, '');
  assert.equal((await direct.load('Exampleville', 'en')).ok, true);
  assert.ok(direct.selectHeading('Geography').body.includes('Example Region'));
  assert.equal(calls, 1);
  assert.deepEqual(direct.hops(), []);
  assert.equal((await direct.load('Exampleville', 'evil.com/')).ok, false);
  assert.equal(calls, 1);
  let attempts = 0;
  const retry = wiki.createSession(async () => {
    if (++attempts === 1) throw new Error('offline');
    return {ok: true, json: async () => fixture};
  });
  assert.equal((await retry.load('Exampleville', 'en')).ok, false);
  assert.equal((await retry.load('Exampleville', 'en')).ok, true);
  let mcpCalls = 0;
  const mcp = wiki.createSession(async (url, init) => {
    mcpCalls++;
    assert.equal(url, 'https://mcp.example/mcp');
    assert.equal(init.method, 'POST');
    throw new Error('offline');
  }, 'https://mcp.example/mcp');
  assert.equal((await mcp.load('Exampleville', 'en')).ok, false);
  assert.equal(mcpCalls, 1); // No automatic direct fallback on MCP failure.
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", script, str(PAGES_WIKI_JS_SOURCE), str(FIXTURE)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_unset_endpoint_generates_direct_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PAGES_WIKI_MCP_URL_ENV, raising=False)
    html = write_pages(tmp_path / "site").read_text(encoding="utf-8")
    assert 'data-mcp-url=""' in html
    assert "disabled (direct browser → Wikipedia)" in html


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("endpoint,expected", [("", 0), ("https://mcp.example/mcp", 0), ("http://mcp.example/mcp", 1)])
def test_public_build_endpoint_guard(event: str, endpoint: str, expected: int) -> None:
    workflow = (ROOT / ".github/workflows/stub-pages.yml").read_text(encoding="utf-8")
    build = workflow.split("- name: Build static Pages tree", 1)[1]
    guard = build.split("run: |", 1)[1].split("          python -m mcp_toolcall_lab.stub_front", 1)[0]
    result = subprocess.run(
        ["bash", "-c", textwrap.dedent(guard)],
        env={**os.environ, "GITHUB_EVENT_NAME": event, "MCP_TOOLCALL_LAB_PAGES_MCP_URL": endpoint},
        capture_output=True, text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr


# ═══ · wikipedia mcp deno ════════════════════════════════════════════════════════════════════════════════
# Wikipedia-only Deno Streamable HTTP MCP: catalog + wire contract.
#
# The Deno server is not FastMCP. Its ``tools/list`` inputSchema must still
# come from ``create_mcp().list_tools()`` (written to
# ``deploy/wikipedia-mcp/catalog.generated.json``). Handshake/SSE/session
# headers are checked against PR #23's recorded ``POST /mcp`` hops so this
# endpoint stays official JSON-RPC 2.0 Streamable HTTP, not a lab event list.

DENO_DIR = ROOT / "deploy" / "wikipedia-mcp"
WIKI_FIXTURE = ROOT / "fixtures" / "wikipedia" / "exampleville_extract.json"
DENO = shutil.which("deno") or str(Path.home() / ".deno" / "bin" / "deno")

WIKIPEDIA_MCP_DENO_MARK = pytest.mark.integration


def _advertised(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "description": spec["description"],
        "inputSchema": spec["inputSchema"],
    }


def _wikipedia_expected_specs() -> list[dict]:
    return [_advertised(spec) for spec in EXPECTED_TOOL_SPECS if spec["name"] in WIKIPEDIA_TOOL_NAMES]


@WIKIPEDIA_MCP_DENO_MARK
def test_generated_catalog_matches_live_fastmcp_wikipedia_specs() -> None:
    recorded = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    live = catalog_document(asyncio.run(load_wikipedia_specs()))
    assert recorded == live
    advertised = [_advertised(spec) for spec in recorded["tools"]]
    assert advertised == _wikipedia_expected_specs()
    assert [spec["name"] for spec in recorded["tools"]] == list(sorted(WIKIPEDIA_TOOL_NAMES))
    assert "find_municipalities" not in [spec["name"] for spec in recorded["tools"]]


@WIKIPEDIA_MCP_DENO_MARK
def test_catalog_export_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "catalog.generated.json"
    write_catalog(path)
    first = path.read_text(encoding="utf-8")
    write_catalog(path)
    assert path.read_text(encoding="utf-8") == first
    assert '"fetch_wikipedia_article"' in first
    assert '"fetch_wikipedia_section"' in first


@WIKIPEDIA_MCP_DENO_MARK
def test_pr23_wire_fixtures_are_jsonrpc_sse_not_lab_events() -> None:
    """Same contract as tests/test_server.py, kept here so Wikipedia MCP CI
    fails if the recorded FastMCP hops stop being JSON-RPC 2.0 Streamable HTTP."""
    found_initialize = False
    for case in iter_cases():
        hops = iter_mcp_hops(case)
        for hop in hops:
            assert "event" not in hop
            http = hop["http"]
            assert http["method"] == "POST"
            assert http["path"] == "/mcp"
            request = hop_request(hop)
            assert request["jsonrpc"] == "2.0"
            method = request["method"]
            if method == MCP_INITIALIZED:
                assert http["status"] == 202
                assert http["response"] is None
                assert (http.get("response_sse") or "") == ""
                continue
            assert http["status"] == 200
            assert http["response_headers"]["Content-Type"] == "text/event-stream"
            assert str(http["response_sse"]).startswith("event: message")
            parsed = extract_sse_data(http["response_sse"])
            assert parsed == hop_response(hop)
            assert parsed["jsonrpc"] == "2.0"
            assert "result" in parsed
            if method == MCP_INITIALIZE:
                found_initialize = True
                assert request["params"]["protocolVersion"] == "2025-06-18"
                assert hop_response(hop)["result"]["protocolVersion"] == "2025-06-18"
                assert http["response_headers"]["Mcp-Session-Id"]
            if method == MCP_TOOLS_LIST:
                tools = hop_response(hop)["result"]["tools"]
                specs = [
                    _advertised(tool)
                    for tool in sorted(tools, key=lambda item: item["name"])
                ]
                # PR #23 fixtures are historical wire captures, not the live catalog.\n                # Keep validating their JSON-RPC/SSE envelope without requiring newly\n                # added tools to be retroactively present in those recordings.\n                expected_by_name = {spec["name"]: spec for spec in EXPECTED_TOOL_SPECS}\n                assert specs == [expected_by_name[spec["name"]] for spec in specs]\n                assert all(spec["name"] in expected_by_name for spec in specs)
            if method == MCP_TOOLS_CALL:
                result = hop_response(hop)["result"]
                assert "isError" in result
                assert result["content"][0]["type"] == "text"
    assert found_initialize


@WIKIPEDIA_MCP_DENO_MARK
def test_python_fastmcp_wikipedia_subset_matches_generated_catalog() -> None:
    with running_mcp_server() as server:
        session_headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        init = httpx.post(
            server.url,
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "wikipedia-mcp-catalog", "version": "0.0.1"},
                },
            },
            timeout=10.0,
        )
        session_id = init.headers.get("mcp-session-id")
        assert session_id
        httpx.post(
            server.url,
            headers={**session_headers, "Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=10.0,
        )
        listed = httpx.post(
            server.url,
            headers={**session_headers, "Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        tools = extract_sse_data(listed.text)["result"]["tools"]
        wiki = [
            _advertised(tool)
            for tool in tools
            if tool["name"] in WIKIPEDIA_TOOL_NAMES
        ]
        wiki.sort(key=lambda item: item["name"])
        recorded = [_advertised(spec) for spec in json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["tools"]]
        assert wiki == recorded


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-limit:]


@dataclass
class RunningDeno:
    url: str
    process: subprocess.Popen[str]


@contextmanager
def running_wikipedia_mcp(
    extra_env: dict[str, str] | None = None,
    *,
    entrypoint: Path | None = None,
) -> Iterator[RunningDeno]:
    if not Path(DENO).is_file():
        pytest.skip("deno is not installed")
    port = _free_port()
    stderr_path = Path(tempfile.mkstemp(prefix="wiki-mcp-stderr-", suffix=".log")[1])
    env = {
        **os.environ,
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        FIXTURE_ENV: str(WIKI_FIXTURE),
    }
    if extra_env:
        env.update(extra_env)
    with stderr_path.open("w", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            [
                DENO,
                "run",
                "--allow-net",
                "--allow-env",
                "--allow-read",
                str(entrypoint) if entrypoint is not None else "main.ts",
            ],
            cwd=DENO_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            text=True,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Deno Wikipedia MCP exited {process.returncode}: {_read_tail(stderr_path)}"
                    )
                try:
                    httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2)
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise RuntimeError(f"Deno Wikipedia MCP did not start: {_read_tail(stderr_path)}")
            yield RunningDeno(url=url, process=process)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    stderr_path.unlink(missing_ok=True)


class DenoMcpSession:
    def __init__(self, url: str) -> None:
        self.url = url
        self.client = httpx.Client(timeout=15.0)
        self.session_id: str | None = None
        self._next_id = 1

    def close(self) -> None:
        self.client.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, payload: dict) -> httpx.Response:
        return self.client.post(self.url, headers=self._headers(), json=payload)

    def initialize(self) -> dict:
        response = self.post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "wikipedia-mcp-test", "version": "0.0.1"},
                },
            }
        )
        self._next_id += 1
        self.session_id = response.headers.get("mcp-session-id")
        assert self.session_id, response.headers
        notify = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert notify.status_code == 202, notify.text
        assert notify.content == b"" or notify.text.strip() == ""
        body = extract_sse_data(response.text)
        return body["result"]


@pytest.fixture()
def deno_session():
    with running_wikipedia_mcp() as server:
        session = DenoMcpSession(server.url)
        session.initialize()
        try:
            yield session
        finally:
            session.close()


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_handshake_matches_pr23_wire_envelope(deno_session: DenoMcpSession) -> None:
    fixture_init = None
    fixture_initialized = None
    for hop in iter_mcp_hops(iter_cases()[0]):
        method = hop_request(hop)["method"]
        if method == MCP_INITIALIZE:
            fixture_init = hop
        elif method == MCP_INITIALIZED:
            fixture_initialized = hop
    assert fixture_init is not None and fixture_initialized is not None
    assert hop_request(fixture_init)["params"]["protocolVersion"] == "2025-06-18"

    response = deno_session.post(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/list",
            "params": {},
        }
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("mcp-session-id") == deno_session.session_id
    assert response.text.startswith("event: message")
    parsed = extract_sse_data(response.text)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] == 99
    assert "result" in parsed
    assert "event" not in parsed
    names = [tool["name"] for tool in parsed["result"]["tools"]]
    assert names == list(sorted(WIKIPEDIA_TOOL_NAMES))
    specs = [_advertised(tool) for tool in parsed["result"]["tools"]]
    assert specs == _wikipedia_expected_specs()
    recorded = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["tools"]
    assert parsed["result"]["tools"] == recorded


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_cors_preflight_allows_browser_mcp_headers() -> None:
    with running_wikipedia_mcp() as server:
        response = httpx.options(
            server.url,
            headers={
                "Origin": "https://myon-bioinformatics.github.io",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,accept,mcp-session-id",
            },
            timeout=10.0,
        )
        assert response.status_code == 204
        assert response.headers.get("access-control-allow-origin") == "*"
        allow = response.headers.get("access-control-allow-headers", "").lower()
        assert "mcp-session-id" in allow
        assert "content-type" in allow
        expose = response.headers.get("access-control-expose-headers", "").lower()
        assert "mcp-session-id" in expose


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_tools_call_article_and_section_use_wikipedia_fixture(deno_session: DenoMcpSession) -> None:
    article = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "fetch_wikipedia_article", "arguments": {"title": "Exampleville"}},
            }
        ).text
    )["result"]
    assert article["isError"] is False
    payload = article["structuredContent"]
    if isinstance(payload, dict) and "result" in payload and "canonical_title" not in payload:
        payload = payload["result"]
    assert payload["canonical_title"] == "Exampleville"
    assert "90001" in payload["extract"]
    assert any(row["heading"] == "Geography" for row in payload["headings"])
    assert json.loads(article["content"][0]["text"]) == payload

    listed = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "fetch_wikipedia_section", "arguments": {"title": "Exampleville"}},
            }
        ).text
    )["result"]
    assert listed["isError"] is False
    rows = listed["structuredContent"]["result"]
    assert {"heading": "Exampleville"} in rows
    assert {"heading": "Geography"} in rows
    assert all("body" not in row for row in rows)

    geography = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_section",
                    "arguments": {"title": "Exampleville", "heading": "Geography"},
                },
            }
        ).text
    )["result"]
    assert geography["isError"] is False
    assert geography["structuredContent"]["result"] == [
        {
            "heading": "Geography",
            "heading_markdown": "## Geography",
            "body": "Example Region, a fictional area. The mock station is JR Exampleville.",
        }
    ]

    climate = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_section",
                    "arguments": {"title": "Exampleville", "heading": "Climate"},
                },
            }
        ).text
    )["result"]
    assert climate["structuredContent"]["result"][0]["heading_markdown"] == "### Climate"

    empty = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_section",
                    "arguments": {"title": "Exampleville", "heading": "no-such-heading"},
                },
            }
        ).text
    )["result"]
    assert empty["isError"] is False
    assert empty["structuredContent"]["result"] == []

    redirected = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_article",
                    "arguments": {"title": "Exampleville, Demo"},
                },
            }
        ).text
    )["result"]
    assert redirected["structuredContent"]["canonical_title"] == "Exampleville"


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_unknown_tool_and_missing_title_are_iserror(deno_session: DenoMcpSession) -> None:
    unknown = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {"name": "find_municipalities", "arguments": {"query": "Exampleville"}},
            }
        ).text
    )["result"]
    assert unknown["isError"] is True
    assert "find_municipalities" in unknown["content"][0]["text"]

    missing = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {"name": "fetch_wikipedia_article", "arguments": {}},
            }
        ).text
    )["result"]
    assert missing["isError"] is True
    assert "title" in missing["content"][0]["text"].lower()

    absent = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_article",
                    "arguments": {"title": "This Title Is Not In The Fixture"},
                },
            }
        ).text
    )["result"]
    assert absent["isError"] is True
    assert "no en.wikipedia.org article" in absent["content"][0]["text"]


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_requires_session_and_does_not_invent_event_rows() -> None:
    with running_wikipedia_mcp() as server:
        listed = httpx.post(
            server.url,
            headers={"Content-Type": "application/json", "Accept": ACCEPT},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        assert listed.status_code == 400
        health = httpx.get(server.url.replace("/mcp", "/health"), timeout=10.0)
        assert health.status_code == 200
        assert health.json() == {"ok": True, "server": "mcp-toolcall-lab-wikipedia"}
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            response = session.post(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
            )
            parsed = extract_sse_data(response.text)
            assert set(parsed) <= {"jsonrpc", "id", "result", "error"}
            assert parsed["jsonrpc"] == "2.0"
        finally:
            session.close()


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_rejects_injected_lang_as_tool_error(deno_session: DenoMcpSession) -> None:
    for lang in ("evil.com/", "evil.com", "#", "@"):
        result = extract_sse_data(
            deno_session.post(
                {
                    "jsonrpc": "2.0",
                    "id": 30,
                    "method": "tools/call",
                    "params": {
                        "name": "fetch_wikipedia_article",
                        "arguments": {"title": "Exampleville", "lang": lang},
                    },
                }
            ).text
        )["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "invalid wikipedia language code" in text
        assert "90001" not in text


@WIKIPEDIA_MCP_DENO_MARK
@pytest.mark.parametrize("offset_ms", [0, 999])
def test_deno_session_expires_after_ttl(tmp_path: Path, offset_ms: int) -> None:
    # Tokens use integer Unix seconds, so a real one-second TTL can expire
    # between initialize and notifications/initialized at a second boundary.
    # Control time only in this test's Deno process; keep the real HTTP handler
    # and HMAC verification, without adding a production clock override.
    now_ms = 1_700_000_000_000 + offset_ms
    expires_ms = (now_ms // 1000 + 1) * 1000
    clock = tmp_path / "clock.txt"
    clock.write_text(str(now_ms), encoding="utf-8")
    entrypoint = tmp_path / "clocked_mcp.ts"
    entrypoint.write_text(
        f'import {{ handleRequest }} from {json.dumps((DENO_DIR / "mcp.ts").as_uri())};\n'
        f"Date.now = () => Number(Deno.readTextFileSync({json.dumps(str(clock))}));\n"
        'Deno.serve({ port: Number(Deno.env.get("MCP_PORT")), hostname: "127.0.0.1" }, handleRequest);\n',
        encoding="utf-8",
    )
    with running_wikipedia_mcp(
        extra_env={"MCP_SESSION_TTL_SECONDS": "1"}, entrypoint=entrypoint
    ) as server:
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            assert session.session_id
            assert int(session.session_id.split(".")[1]) * 1000 == expires_ms
            clock.write_text(str(expires_ms - 1), encoding="utf-8")
            listed = session.post(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
            assert listed.status_code == 200, listed.text
            for request_id, expired_ms in enumerate((expires_ms, expires_ms + 1000), start=3):
                clock.write_text(str(expired_ms), encoding="utf-8")
                expired = session.post(
                    {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}}
                )
                assert expired.status_code == 400, expired.text
                body = expired.json()
                assert body["error"]["code"] == -32000
                assert "session" in body["error"]["message"].lower()
        finally:
            session.close()


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_hmac_session_is_accepted_by_a_second_process() -> None:
    """Tokens are HMAC + TTL, not KV or an isolate-local Set — a second
    process with the same MCP_SESSION_SECRET must accept the first process's id."""

    secret = {"MCP_SESSION_SECRET": "lab-test-mcp-session-secret"}
    with running_wikipedia_mcp(secret) as first:
        session = DenoMcpSession(first.url)
        try:
            session.initialize()
            token = session.session_id
            assert token and token.startswith("sess_")
            with running_wikipedia_mcp(secret) as second:
                listed = httpx.post(
                    second.url,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": ACCEPT,
                        "Mcp-Session-Id": token,
                    },
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    timeout=10.0,
                )
            assert listed.status_code == 200
            parsed = extract_sse_data(listed.text)
            assert [tool["name"] for tool in parsed["result"]["tools"]] == list(
                sorted(WIKIPEDIA_TOOL_NAMES)
            )
        finally:
            session.close()


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_tampered_session_is_rejected() -> None:
    with running_wikipedia_mcp({"MCP_SESSION_SECRET": "lab-test-mcp-session-secret"}) as server:
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            token = session.session_id
            assert token
            parts = token.split(".")
            assert len(parts) == 3
            mac = parts[2]
            parts[2] = ("B" if mac.startswith("A") else "A") + mac[1:]
            flipped = ".".join(parts)
            listed = httpx.post(
                server.url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": ACCEPT,
                    "Mcp-Session-Id": flipped,
                },
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                timeout=10.0,
            )
            assert listed.status_code == 400
            body = listed.json()
            assert body["error"]["code"] == -32000
            assert "Mcp-Session-Id" in body["error"]["message"]
        finally:
            session.close()


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_unknown_session_id_is_rejected() -> None:
    with running_wikipedia_mcp() as server:
        listed = httpx.post(
            server.url,
            headers={
                "Content-Type": "application/json",
                "Accept": ACCEPT,
                "Mcp-Session-Id": "sess_not-a-real-session",
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        assert listed.status_code == 400
        body = listed.json()
        assert body["error"]["code"] == -32000
        assert "Mcp-Session-Id" in body["error"]["message"]


@WIKIPEDIA_MCP_DENO_MARK
def test_deno_wikipedia_fetch_rate_limit() -> None:
    with running_wikipedia_mcp(extra_env={"MCP_WIKI_FETCH_LIMIT_PER_MINUTE": "1"}) as server:
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_article",
                    "arguments": {"title": "Exampleville"},
                },
            }
            first = extract_sse_data(session.post({**payload, "id": 40}).text)["result"]
            assert first["isError"] is False
            second = extract_sse_data(session.post({**payload, "id": 41}).text)["result"]
            assert second["isError"] is True
            assert "rate limit" in second["content"][0]["text"].lower()
        finally:
            session.close()
