"""Pages/Actions stub stack — no Docker in default pytest."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_toolcall_lab.antipatterns import (
    CPU_LLM_UNREACHABLE,
    MCP_UNREACHABLE,
    classify_stub_turn,
)
from mcp_toolcall_lab.markdown_lib import (
    assert_markdown_provenance,
    load_markdown,
    markdown_py_path,
)
from mcp_toolcall_lab.stub_front import (
    BUILD_META_KEYS,
    BUILD_META_NAME,
    MCP_PATTERNS,
    PAGES_FORBIDDEN_NAMES,
    PAGES_HASH_JS_NAME,
    PAGES_HASH_JS_SOURCE,
    PAGES_SUMMARY_KEYS,
    PAGES_WIKI_SERVE,
    STUB_DEMO_DATA_NAME,
    STUB_DEMO_JS_NAME,
    STUB_DEMO_JS_SOURCE,
    _status_is_dirty,
    collect_revision,
    load_corpus,
    pages_summary,
    parse_sections,
    render_rows,
    write_pages,
    write_stub_demo_page,
)

ROOT = Path(__file__).resolve().parents[1]


def test_vendored_markdown_py_is_loadable() -> None:
    path = markdown_py_path()
    assert path is not None
    md = load_markdown()
    assert md is not None
    assert md.split_sections("# Yokohama\n\nbody\n")[0]["title"] == "Yokohama"
    recorded = assert_markdown_provenance()
    assert recorded["commit"] == "f3c0bf82a7d0f0e9653a224d7055911e3068b7ff"
    assert recorded["blob_sha"] == "ad3b9f8e81dafd8e17bb40035c390e42e69869ad"
    readme = (ROOT / "vendor" / "README.md").read_text(encoding="utf-8")
    assert "ref=${COMMIT}" in readme
    assert "not `main`" in readme


def test_parse_sections_uses_markdown_py_bodies() -> None:
    sections = parse_sections("# Find municipalities\n\nUse the mock tool.\n")
    assert sections[0].title == "Find municipalities"
    assert "mock tool" in sections[0].body


def test_render_rows_uses_markdown_table() -> None:
    text = render_rows([{"name": "Yokohama", "code": "14109"}])
    assert "Yokohama" in text
    assert "14109" in text


_FAKE_REVISION = {
    "version": None,
    "sha": "abcdef1234567890abcdef1234567890abcdef12",
    "shortSha": "abcdef12",
    "ref": "main",
    "committedAt": "2026-09-19T16:50:27Z",
    "subject": "Simplify Pages report",
    "commitUrl": "https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/abcdef1234567890abcdef1234567890abcdef12",
    "dirty": False,
}


def test_write_pages_is_static(tmp_path: Path) -> None:
    last = tmp_path / "last-run.json"
    last.write_text('{"cpu_llm_ok": true, "stub_health": {"status": 200}}\n', encoding="utf-8")
    index = write_pages(tmp_path / "site", last_run=last)
    html = index.read_text(encoding="utf-8")
    assert "mcp-toolcall-lab stub" in html
    assert "mcp-mock:8000/mcp" in html
    assert "Commit " in html
    assert 'id="build-meta"' in html
    assert (tmp_path / "site" / "summary.json").is_file()
    assert (tmp_path / "site" / BUILD_META_NAME).is_file()
    assert not (tmp_path / "site" / "last-run.json").exists()


def test_pages_tree_excludes_raw_mcp_logs(tmp_path: Path) -> None:
    last = tmp_path / "last-run.json"
    last.write_text(
        json.dumps(
            {
                "turn": {
                    "case": "MCP_SUCCESS",
                    "assistant": "Yokohama",
                    "arguments": {"query": "secret-prompt"},
                    "chat_id": "chat_secret",
                    "meta": {"chat_id": "chat_secret"},
                },
                "observation": {"verdict": "PASS", "antipattern_id": None},
                "cpu_llm_ok": True,
                "cpu_llm_backend": "lite-stub",
                "cpu_llm_completion_ok": True,
                "stub_health": {"status": 200, "body": "ok"},
                "turn_http": 200,
            }
        ),
        encoding="utf-8",
    )
    anti = tmp_path / "antipatterns.jsonl"
    anti.write_text(
        '{"verdict":"PASS","detail":"reached MCP","chat_id":"chat_secret"}\n',
        encoding="utf-8",
    )
    out = tmp_path / "site"
    out.mkdir()
    (out / "mcp-toolcalls.jsonl").write_text("{}\n", encoding="utf-8")
    write_pages(out, last_run=last, observations=anti)
    names = {path.name for path in out.iterdir()}
    assert names.isdisjoint(PAGES_FORBIDDEN_NAMES)
    public = (out / "index.html").read_text(encoding="utf-8") + (out / "summary.json").read_text(
        encoding="utf-8"
    )
    assert "secret-prompt" not in public
    assert "chat_secret" not in public
    assert '"arguments"' not in public
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert set(summary) == set(PAGES_SUMMARY_KEYS)
    assert summary["verdict"] == "PASS"
    assert summary["showed_expected_fragment"] is True
    assert summary["cpu_llm_backend"] == "lite-stub"
    assert summary["cpu_llm_completion_ok"] is True
    assert pages_summary({"turn": {"case": "HEADING_MISS"}}, [])["case"] == "HEADING_MISS"


def test_classify_stub_turn_pass_and_miss() -> None:
    ok = classify_stub_turn(
        {"case": "MCP_SUCCESS", "assistant": "Yokohama 14109"}, cpu_llm_ok=True
    )
    assert ok["verdict"] == "PASS"
    assert ok["cpu_llm_ok"] is True
    miss = classify_stub_turn({"case": "MCP_UNREACHABLE", "assistant": "timed out"})
    assert miss["antipattern_id"] == MCP_UNREACHABLE
    down = classify_stub_turn(
        {"case": "MCP_SUCCESS", "assistant": "Yokohama"}, cpu_llm_ok=False
    )
    assert down["cpu_llm_ok"] is False
    assert CPU_LLM_UNREACHABLE != down["antipattern_id"]


def test_stub_pages_compose_uses_service_dns() -> None:
    compose = (ROOT / "docker" / "stub-pages" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "name: mcp-toolcall-lab-pages" in compose
    assert "STUB_MCP_URL: http://mcp-mock:8000/mcp" in compose
    assert "STUB_CPU_LLM_URL: http://cpu-llm:8080/v1" in compose
    assert "host.docker.internal" not in compose
    stub_df = (ROOT / "docker" / "stub-pages" / "Dockerfile.stub").read_text(encoding="utf-8")
    assert "pip install" not in stub_df
    assert "vendor/markdown.py" in stub_df
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "actions/deploy-pages" in workflow
    assert workflow.count("if: github.ref == 'refs/heads/main'") >= 2
    assert "stub-pages-smoke.py" in workflow or "stub_pages_smoke.py" in workflow
    assert "mcp_toolcall_lab.docker_logs" in workflow
    assert "test-results/docker-logs" in workflow
    assert "_site/mcp-toolcalls.jsonl" not in workflow
    assert "_site/antipatterns.jsonl" not in workflow
    assert "2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d" in workflow
    assert "build: !reset" in (
        ROOT / "docker" / "stub-pages" / "docker-compose.gguf.yml"
    ).read_text(encoding="utf-8")


def test_gguf_overlay_pins_image_digest_and_uses_curl_healthcheck() -> None:
    overlay = (ROOT / "docker" / "stub-pages" / "docker-compose.gguf.yml").read_text(
        encoding="utf-8"
    )
    provenance = json.loads(
        (ROOT / "docker" / "stub-pages" / "llama.cpp.image.provenance.json").read_text(
            encoding="utf-8"
        )
    )
    digest = provenance["digest"]
    assert digest.startswith("sha256:")
    assert f"ghcr.io/ggml-org/llama.cpp:server@{digest}" in overlay
    assert "build: !reset" in overlay
    assert "environment: !reset" in overlay
    assert "/dev/tcp" not in overlay
    assert 'test: ["CMD", "curl", "-f", "http://127.0.0.1:8080/health"]' in overlay
    assert provenance["healthcheck"] == ["CMD", "curl", "-f", "http://127.0.0.1:8080/health"]
    model_pin = json.loads(
        (ROOT / "docker" / "stub-pages" / "models" / "model.gguf.provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert model_pin["sha256_verified_against_pin"] is True
    assert model_pin["sha256"] == "2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d"


def test_write_pages_does_not_embed_the_static_try_it_demo(tmp_path: Path) -> None:
    """Published Pages is generation + /wiki induction + CI summary.

    The mock heading-pulldown demo is a local asset (write_stub_demo_page),
    not the github.io index — that pulldown was a fixtures/stub_front corpus,
    not Wikipedia, and read as a stub Wiki UI.
    """
    out = write_pages(tmp_path / "site", revision=_FAKE_REVISION).parent
    html = (out / "index.html").read_text(encoding="utf-8")
    names = {path.name for path in out.iterdir()}
    assert STUB_DEMO_JS_NAME not in names
    assert STUB_DEMO_DATA_NAME not in names
    assert 'id="stub-demo"' not in html
    assert "Try it (static, no MCP)" not in html
    assert f'src="{STUB_DEMO_JS_NAME}"' not in html
    assert STUB_DEMO_DATA_NAME not in html
    assert "Corpus headings" not in html
    assert "mcpToolcallLabStubDemo" not in html
    assert PAGES_HASH_JS_NAME in names
    assert f'src="{PAGES_HASH_JS_NAME}"' in html
    assert (out / PAGES_HASH_JS_NAME).read_text(encoding="utf-8") == (
        PAGES_HASH_JS_SOURCE.read_text(encoding="utf-8")
    )


def test_write_pages_has_hash_routed_home_and_wiki_panels(tmp_path: Path) -> None:
    """Published index stays one endpoint: #wiki is an in-page transition."""
    html = write_pages(tmp_path / "site", revision=_FAKE_REVISION).read_text(encoding="utf-8")
    assert 'id="pages-nav"' in html
    assert 'data-testid="pages-nav-home" href="#"' in html or (
        'href="#" data-pages-nav="home" data-testid="pages-nav-home"' in html
    )
    assert 'href="#wiki"' in html
    assert 'data-pages-nav="wiki"' in html
    assert 'data-testid="pages-nav-wiki"' in html
    assert 'id="home"' in html
    assert 'id="wiki"' in html
    assert 'data-pages-view="home"' in html
    assert 'data-pages-view="wiki"' in html
    assert 'data-testid="pages-home"' in html
    assert 'data-testid="pages-wiki"' in html
    assert 'data-testid="pages-nav-back"' in html
    assert "Back to report" in html
    assert 'id="build-meta"' in html
    home_idx = html.index('data-pages-view="home"')
    wiki_idx = html.index('data-pages-view="wiki"')
    assert html.index('id="build-meta"') > home_idx
    assert html.index('id="build-meta"') < wiki_idx
    assert html.index("<h2>Last Actions summary</h2>") > home_idx
    assert html.index("<h2>Last Actions summary</h2>") < wiki_idx
    assert html.index(PAGES_WIKI_SERVE.splitlines()[0]) > wiki_idx
    assert "github.io cannot host" in html[wiki_idx:]
    assert "http://127.0.0.1:8765/wiki" in html[wiki_idx:]
    wiki_open = html.find("<section", wiki_idx - 80, wiki_idx + 80)
    assert wiki_open != -1
    wiki_tag = html[wiki_open : html.find(">", wiki_open) + 1]
    assert " hidden" in wiki_tag
    assert 'action="/wiki"' not in html
    assert 'href="/wiki"' not in html
    assert 'id="stub-demo"' not in html
    assert "Try it (static, no MCP)" not in html
    assert "Corpus headings" not in html


_PAGES_HASH_CONTRACT = """
const pages = require(process.argv[1]);
const assert = require("assert");

assert.strictEqual(pages.viewFromLocation("", ""), "home");
assert.strictEqual(pages.viewFromLocation("#", ""), "home");
assert.strictEqual(pages.viewFromLocation("#home", ""), "home");
assert.strictEqual(pages.viewFromLocation("#wiki", ""), "wiki");
assert.strictEqual(pages.viewFromLocation("#wiki?x=1", ""), "wiki");
assert.strictEqual(pages.viewFromLocation("", "?view=wiki"), "wiki");
assert.strictEqual(pages.viewFromLocation("#", "?view=wiki"), "wiki");
assert.strictEqual(pages.viewFromLocation("", "?foo=1&view=wiki"), "wiki");
assert.strictEqual(pages.viewFromLocation("", "?view=home"), "home");
assert.strictEqual(pages.viewFromLocation("#other", ""), "home");
assert.strictEqual(pages.viewFromLocation("#wiki", "?view=home"), "wiki");

function el(attr, value) {
  const attrs = {};
  attrs[attr] = value;
  return {
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
    setAttribute(name, next) {
      attrs[name] = String(next);
    },
    removeAttribute(name) {
      delete attrs[name];
    },
    hasAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name);
    },
  };
}

const home = el("data-pages-view", "home");
const wiki = el("data-pages-view", "wiki");
const navHome = el("data-pages-nav", "home");
const navWiki = el("data-pages-nav", "wiki");
const doc = {
  querySelectorAll(sel) {
    if (sel === "[data-pages-view]") return [home, wiki];
    if (sel === "[data-pages-nav]") return [navHome, navWiki];
    return [];
  },
};
pages.applyView(doc, "wiki");
assert.strictEqual(home.hasAttribute("hidden"), true);
assert.strictEqual(wiki.hasAttribute("hidden"), false);
assert.strictEqual(navWiki.getAttribute("aria-current"), "page");
assert.strictEqual(navHome.hasAttribute("aria-current"), false);
pages.applyView(doc, "home");
assert.strictEqual(home.hasAttribute("hidden"), false);
assert.strictEqual(wiki.hasAttribute("hidden"), true);
assert.strictEqual(navHome.getAttribute("aria-current"), "page");
assert.strictEqual(navWiki.hasAttribute("aria-current"), false);

let replaced = "";
const win = {
  location: {
    href: "https://example.test/mcp-toolcall-lab/?view=wiki",
    hash: "",
    search: "?view=wiki",
    pathname: "/mcp-toolcall-lab/",
  },
  history: {
    replaceState(_state, _title, url) {
      replaced = url;
    },
  },
  document: doc,
};
assert.strictEqual(pages.syncFromLocation(win), "wiki");
assert.ok(replaced.includes("#wiki"));
assert.ok(!replaced.includes("view=wiki"));
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_hash_js_view_and_apply_contracts() -> None:
    result = subprocess.run(
        ["node", "-e", _PAGES_HASH_CONTRACT, str(PAGES_HASH_JS_SOURCE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_hash_js_is_valid_javascript() -> None:
    result = subprocess.run(
        ["node", "--check", str(PAGES_HASH_JS_SOURCE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_write_pages_emits_build_meta_and_commit(tmp_path: Path) -> None:
    out = write_pages(tmp_path / "site", revision=_FAKE_REVISION).parent
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="build-meta"' in html
    assert "Commit abcdef12" in html
    assert "Version " not in html
    assert _FAKE_REVISION["commitUrl"] in html
    assert _FAKE_REVISION["committedAt"] in html
    assert _FAKE_REVISION["subject"] in html
    assert _FAKE_REVISION["sha"] in html
    assert "python -m mcp_toolcall_lab.stub_front serve --port 8765" in html
    assert PAGES_WIKI_SERVE.splitlines()[0] in html
    assert "/wiki" in html
    assert "<h2>Last Actions summary</h2>" in html
    assert "not live Wikipedia.## Last Actions" not in html
    meta_path = out / BUILD_META_NAME
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert set(meta) == set(BUILD_META_KEYS)
    assert meta["shortSha"] == "abcdef12"
    assert meta["sha"] == _FAKE_REVISION["sha"]
    assert meta["commitUrl"] == _FAKE_REVISION["commitUrl"]
    assert meta["version"] is None


def test_write_pages_shows_version_only_when_present(tmp_path: Path) -> None:
    revision = {**_FAKE_REVISION, "version": "9.9.9"}
    html = write_pages(tmp_path / "site", revision=revision).read_text(encoding="utf-8")
    assert "Version 9.9.9" in html
    assert "Commit abcdef12" in html


def _stub_git_status(monkeypatch, porcelain: str) -> None:
    def fake_git(args: list[str]) -> str | None:
        table = {
            ("rev-parse", "HEAD"): "deadbeefcafebabe000000000000000000000000",
            ("rev-parse", "--short=8", "HEAD"): "deadbeef",
            ("branch", "--show-current"): "main",
            ("show", "-s", "--format=%cI", "HEAD"): "2026-09-19T12:00:00+00:00",
            ("show", "-s", "--format=%s", "HEAD"): "subject",
            ("status", "--porcelain"): porcelain,
        }
        return table.get(tuple(args))

    monkeypatch.setattr("mcp_toolcall_lab.stub_front._git_output", fake_git)


def test_collect_revision_prefers_github_actions_env(monkeypatch) -> None:
    def fake_git(args: list[str]) -> str | None:
        table = {
            ("rev-parse", "HEAD"): "gitsha0000000000000000000000000000000000",
            ("rev-parse", "--short=8", "HEAD"): "gitsha00",
            ("branch", "--show-current"): "local-branch",
            ("show", "-s", "--format=%cI", "HEAD"): "2026-01-01T00:00:00+00:00",
            ("show", "-s", "--format=%s", "HEAD"): "local subject",
            ("status", "--porcelain"): " M stub_front.py",
        }
        return table.get(tuple(args))

    monkeypatch.setattr("mcp_toolcall_lab.stub_front._git_output", fake_git)
    meta = collect_revision(
        {
            "GITHUB_SHA": "actions1234567890abcdef1234567890abcdef12",
            "GITHUB_REF_NAME": "main",
            "GITHUB_REPOSITORY": "myon-bioinformatics/mcp-toolcall-lab",
            "GITHUB_SERVER_URL": "https://github.com",
        }
    )
    assert meta["sha"] == "actions1234567890abcdef1234567890abcdef12"
    assert meta["shortSha"] == "actions1"
    assert meta["ref"] == "main"
    assert meta["commitUrl"] == (
        "https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/"
        "actions1234567890abcdef1234567890abcdef12"
    )
    assert meta["committedAt"] == "2026-01-01T00:00:00+00:00"
    assert meta["subject"] == "local subject"
    assert meta["dirty"] is True
    assert meta["version"] is None
    assert set(meta) == set(BUILD_META_KEYS)


def test_collect_revision_falls_back_to_git_when_actions_env_absent(monkeypatch) -> None:
    def fake_git(args: list[str]) -> str | None:
        table = {
            ("rev-parse", "HEAD"): "deadbeefcafebabe000000000000000000000000",
            ("rev-parse", "--short=8", "HEAD"): "deadbeef",
            ("branch", "--show-current"): "cursor/pages-generation-wiki-induction",
            ("show", "-s", "--format=%cI", "HEAD"): "2026-09-19T12:00:00+00:00",
            ("show", "-s", "--format=%s", "HEAD"): "Add Pages revision identity",
            ("status", "--porcelain"): "",
        }
        return table.get(tuple(args))

    monkeypatch.setattr("mcp_toolcall_lab.stub_front._git_output", fake_git)
    meta = collect_revision({})
    assert meta["sha"] == "deadbeefcafebabe000000000000000000000000"
    assert meta["shortSha"] == "deadbeef"
    assert meta["ref"] == "cursor/pages-generation-wiki-induction"
    assert meta["commitUrl"] is None
    assert meta["dirty"] is False
    assert meta["subject"] == "Add Pages revision identity"
    assert meta["version"] is None


def test_collect_revision_git_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("mcp_toolcall_lab.stub_front._git_output", lambda args: None)
    meta = collect_revision({})
    assert meta["sha"] is None
    assert meta["shortSha"] is None
    assert meta["ref"] is None
    assert meta["committedAt"] is None
    assert meta["subject"] is None
    assert meta["commitUrl"] is None
    assert meta["dirty"] is False
    assert meta["version"] is None


def test_collect_revision_reads_package_version_when_present(monkeypatch) -> None:
    monkeypatch.setattr("mcp_toolcall_lab.stub_front._git_output", lambda args: None)
    monkeypatch.setattr("mcp_toolcall_lab.__version__", "9.9.9", raising=False)
    meta = collect_revision({})
    assert meta["version"] == "9.9.9"


def test_collect_revision_ignores_default_site_output(monkeypatch) -> None:
    _stub_git_status(
        monkeypatch,
        "?? _site/index.html\n?? _site/build_meta.json\n?? _site/\n",
    )
    meta = collect_revision({})
    assert meta["dirty"] is False


def test_collect_revision_ignores_custom_out_dir(monkeypatch) -> None:
    _stub_git_status(monkeypatch, "?? tmp-pages/index.html\n?? tmp-pages/summary.json\n")
    meta = collect_revision({}, ignore_paths=(Path("tmp-pages"),))
    assert meta["dirty"] is False


def test_collect_revision_dirty_when_source_changes_alongside_site(monkeypatch) -> None:
    _stub_git_status(
        monkeypatch,
        "?? _site/index.html\n M README.md\n",
    )
    meta = collect_revision({})
    assert meta["dirty"] is True


def test_write_pages_dirty_matches_source_tree_not_its_output() -> None:
    """Regenerating Pages under the real worktree must not flip dirty by itself."""
    out = ROOT / "_site_revision_probe"
    try:
        write_pages(out)
        first = json.loads((out / BUILD_META_NAME).read_text(encoding="utf-8"))
        write_pages(out)
        meta = json.loads((out / BUILD_META_NAME).read_text(encoding="utf-8"))
        assert meta["dirty"] is first["dirty"]
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert meta["dirty"] is _status_is_dirty(porcelain, (out,))
        html = (out / "index.html").read_text(encoding="utf-8")
        if meta["dirty"]:
            assert "(dirty)" in html
        else:
            assert "(dirty)" not in html
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_write_pages_dirty_when_untracked_source_exists() -> None:
    out = ROOT / "_site_revision_probe"
    probe = ROOT / "_revision_source_probe.txt"
    try:
        probe.write_text("untracked source change\n", encoding="utf-8")
        write_pages(out)
        meta = json.loads((out / BUILD_META_NAME).read_text(encoding="utf-8"))
        assert meta["dirty"] is True
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "(dirty)" in html
    finally:
        probe.unlink(missing_ok=True)
        shutil.rmtree(out, ignore_errors=True)


def test_write_stub_demo_page_is_local_only(tmp_path: Path) -> None:
    out = write_stub_demo_page(tmp_path / "demo").parent
    data_path = out / STUB_DEMO_DATA_NAME
    js_path = out / STUB_DEMO_JS_NAME
    assert data_path.is_file()
    assert js_path.is_file()
    demo_data = json.loads(data_path.read_text(encoding="utf-8"))
    expected = [{"title": s.title, "slug": s.slug, "body": s.body} for s in load_corpus()]
    assert demo_data == expected
    assert js_path.read_text(encoding="utf-8") == STUB_DEMO_JS_SOURCE.read_text(encoding="utf-8")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="stub-demo"' in html
    assert f'src="{STUB_DEMO_JS_NAME}"' in html
    for row in demo_data:
        assert set(row) == {"title", "slug", "body"}


def test_stub_demo_js_mirrors_mcp_patterns_tools_and_tokens() -> None:
    """Regression guard: the JS MCP_PATTERNS list is hand-mirrored from
    stub_front.py's (no shared source, since the JS has no MCP client to
    exercise) -- catch drift if one changes without the other."""
    js_source = STUB_DEMO_JS_SOURCE.read_text(encoding="utf-8")
    for tokens, tool, _args_fn in MCP_PATTERNS:
        assert f'"{tool}"' in js_source, f"tool {tool!r} missing from stub_demo.js"
        for token in tokens:
            if token == tool:
                continue  # the tool name itself is already asserted above
            assert token in js_source, f"token {token!r} for {tool!r} missing from stub_demo.js"


def test_stub_demo_js_never_fabricates_an_mcp_result() -> None:
    js_source = STUB_DEMO_JS_SOURCE.read_text(encoding="utf-8")
    # The one MCP-shaped branch must say it has no server, not return rows.
    assert "no MCP server behind it" in js_source
    assert "will not fabricate" in js_source


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_stub_demo_js_is_valid_javascript() -> None:
    result = subprocess.run(
        ["node", "--check", str(STUB_DEMO_JS_SOURCE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
