"""Pages/Actions stub stack, its smoke script, the stdlib stub front, frontends and markdown rendering: no Docker."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab.antipatterns import (
    AUTH_BLOCKED, CPU_LLM_UNREACHABLE, KNOWN_IDS, MCP_NOT_CALLED, MCP_PICKER_OFF, MCP_UNREACHABLE, UI_NO_RESULT, VERDICT_ANTIPATTERN, VERDICT_PASS,
    classify_observation, classify_stub_turn, write_observation,
)
from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS as AVAILABLE_TOOLS_frontends, TOOL_DESCRIPTIONS
from mcp_toolcall_lab.chat_ui import describe, wait_for_assistant
from mcp_toolcall_lab.frontends import FRONTENDS, LIBRECHAT, OPENWEBUI, PRODUCT_CLIENTS, REFERENCE_CLIENTS, STUB, catalog, get_frontend
from mcp_toolcall_lab.markdown_lib import (
    assert_markdown_provenance, load_markdown, markdown_py_path, own_body, parse_sections as parse_sections_markdown_lib,
)
from mcp_toolcall_lab.pixiv_dictionary_tool import PixivDictionaryFetchError
from mcp_toolcall_lab.stub_front import (
    AVAILABLE_TOOLS, BUILD_META_KEYS, BUILD_META_NAME, CASE_HEADING_HIT, CASE_HEADING_MISS, CASE_MCP_EMPTY, CASE_MCP_SUCCESS, DEFAULT_CORPUS,
    LIBRECHAT_INPUT, LIBRECHAT_SEND, MCP_PATTERNS, OWUI_INPUT, OWUI_RESPONSE, OWUI_SEND, PAGES_FORBIDDEN_NAMES, PAGES_HASH_JS_NAME,
    PAGES_HASH_JS_SOURCE, PAGES_PIXIV_JS_NAME, PAGES_PIXIV_JS_SOURCE, PAGES_SUMMARY_KEYS, PAGES_WIKI_BROWSER_NOTE, PAGES_WIKI_JS_NAME,
    PAGES_WIKI_JS_SOURCE, PAGES_WIKI_SERVE, PIXIV_LOCAL_SERVE, SOURCE_JS_NAME, STUB_DEMO_DATA_NAME, STUB_DEMO_JS_NAME, STUB_DEMO_JS_SOURCE,
    StubState, TERMINAL_CSS_NAME, TERMINAL_CSS_SOURCE, _assistant_html, _status_is_dirty, classify_prompt, collect_revision, load_corpus,
    load_pysaiyanweboo, lookup_heading, main, make_handler, pages_summary, parse_sections, pixiv_page_response, pokemon_spec, render_pixiv_page,
    render_rows, render_wiki_page, reply, write_pages, write_stub_demo_page,
)


# ═══ · stub pages ════════════════════════════════════════════════════════════════════════════════════════
# Pages/Actions stub stack — no Docker in default pytest.

def _locked(destination):
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
    return next(e for e in lock["files"] if e["destination"] == destination)


def test_vendored_markdown_py_is_loadable() -> None:
    path = markdown_py_path()
    assert path is not None
    md = load_markdown()
    assert md is not None
    assert md.split_sections("# Exampleville\n\nbody\n")[0]["title"] == "Exampleville"
    recorded = assert_markdown_provenance()
    assert recorded["commit"] == _locked('vendor/markdown.py')['commit']
    assert recorded["blob_sha"] == _locked('vendor/markdown.py')['blob_sha']
    assert recorded["sha256"] == _locked('vendor/markdown.py')['sha256']
    readme = (ROOT / "vendor" / "README.md").read_text(encoding="utf-8")
    assert "vendor.lock.json" in readme
    assert "test_mcp_toolcall_lab_vendor_lock.py" in readme
    # Vendored API surface through the current markdown.py snapshot must be present.
    assert hasattr(md, "markdown_to_html")
    assert hasattr(md, "html_to_markdown")
    assert hasattr(md, "default_stylesheet")
    assert hasattr(md, "alert_stylesheet")
    assert hasattr(md, "markdown_to_kramdown")
    assert hasattr(md, "kramdown_to_markdown")
    assert hasattr(md, "ial")
    assert hasattr(md, "with_attributes")
    assert hasattr(md, "markdown_to_py_percent")
    assert hasattr(md, "markdown_headings_to_mermaid_mindmap")
    assert hasattr(md, "markdown_links_to_dot")
    assert hasattr(md, "inspect_to_markdown")
    assert hasattr(md, "argparse_to_markdown")
    assert hasattr(md, "distribution_to_markdown")
    html = md.markdown_to_html("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    assert "<table>" in html
    assert md.html_to_markdown("<table><tr><th>a</th></tr><tr><td>1</td></tr></table>").count("|") >= 2
    assert "~~gone~~" in md.html_to_markdown("<del>gone</del>")
    kramdown = md.markdown_to_kramdown('# Title {#intro .hero}\n')
    assert "{: #intro .hero}" in kramdown
    assert "#intro" in md.ial(id="intro")
    assert ".markdown-alert" in md.default_stylesheet()


def test_parse_sections_uses_markdown_py_bodies() -> None:
    sections = parse_sections("# Find municipalities\n\nUse the mock tool.\n")
    assert sections[0].title == "Find municipalities"
    assert "mock tool" in sections[0].body


def test_render_rows_uses_markdown_table() -> None:
    text = render_rows([{"name": "Exampleville", "code": "90001"}])
    assert "Exampleville" in text
    assert "90001" in text


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
    # A fixed revision: otherwise the page carries HEAD's commit subject, and a subject that
    # merely mentions the shared-UI repository would trip the "no CDN chain" check below.
    index = write_pages(tmp_path / "site", last_run=last, revision=_FAKE_REVISION)
    html = index.read_text(encoding="utf-8")
    assert "mcp-toolcall-lab stub" in html
    assert "mcp-mock:8000/mcp" in html
    assert "Commit " in html
    assert 'id="build-meta"' in html
    assert (tmp_path / "site" / "summary.json").is_file()
    assert (tmp_path / "site" / BUILD_META_NAME).is_file()
    assert 'class="ui-page term-shell"' in html
    assert 'class="term-header"' in html
    assert 'class="term-workspace"' in html
    assert 'class="ui-panel term-result"' in html
    assert 'class="ui-card term-evidence"' in html
    assert 'class="ui-card term-history"' in html
    assert '<aside aria-label="Supporting information">' in html
    # Single CSS contract: one local stylesheet (generated from PySaiyanWeBoo), no CDN chain.
    assert (tmp_path / "site" / TERMINAL_CSS_NAME).is_file()
    assert f'<link rel="stylesheet" href="{TERMINAL_CSS_NAME}?v=' in html
    assert "cdn.jsdelivr.net" not in html and html.count("<link") == 1
    assert f'generated into the local <code>{TERMINAL_CSS_NAME}</code>' in html and '<body data-ui-theme="terminal">' in html
    assert html.index('class="ui-panel term-result"') < html.index('<aside aria-label="Supporting information">')
    pixiv_tools = {tool for tool in AVAILABLE_TOOLS if tool.startswith("fetch_pixiv_dictionary_")}
    other_tools = set(AVAILABLE_TOOLS) - pixiv_tools
    assert len(AVAILABLE_TOOLS) == 7
    assert f"<span class=\"ui-tag\">{len(AVAILABLE_TOOLS)} tools</span>" in html
    assert pixiv_tools == {
        "fetch_pixiv_dictionary_section",
        "fetch_pixiv_dictionary_article",
    }
    assert len(other_tools) == 5
    for tool in AVAILABLE_TOOLS:
        assert f"<code>{tool}</code>" in html
    assert not (tmp_path / "site" / "last-run.json").exists()


def test_write_pages_embeds_vendor_stylesheet_not_lab_css(tmp_path: Path) -> None:
    """Pages CSS for converted Markdown comes from vendor/markdown.py."""
    md = load_markdown()
    assert md is not None
    html = write_pages(tmp_path / "site", revision=_FAKE_REVISION).read_text(encoding="utf-8")
    vendor_css = md.default_stylesheet()
    assert vendor_css
    assert vendor_css in html
    assert "@layer markdown,base,components,theme;@layer markdown{" in html
    assert html.index("<style>") < html.index("<title>")
    # Lab must not ship a second markdown-feature stylesheet next to Pages.
    assert not (ROOT / "src" / "mcp_toolcall_lab" / "static" / "markdown.css").exists()


def test_single_css_contract_across_pages_wiki_and_pixiv(tmp_path: Path) -> None:
    """Pages, local /wiki, and local /pixiv share one local stylesheet.

    No PySaiyanWeBoo CDN chain anywhere in these three rendered surfaces (the
    look comes from the committed ``terminal.css``, generated from
    ``pysaiyanweboo.core_css("terminal")``), every page opts into that theme, and
    exactly one ``terminal.css`` link per page (the inlined vendor markdown
    stylesheet is a separate, content-scoped concern and is not "the CSS chain").
    """
    assert TERMINAL_CSS_SOURCE.is_file()
    pages_html = write_pages(tmp_path / "site", revision=_FAKE_REVISION).read_text(encoding="utf-8")
    wiki_html = render_wiki_page()
    pixiv_html = render_pixiv_page()
    for html in (pages_html, wiki_html, pixiv_html):
        assert "cdn.jsdelivr.net" not in html and "<link" in html
        assert html.count('data-ui-theme="terminal"') == 1
        assert html.count(f'href="{TERMINAL_CSS_NAME}') + html.count(f'href="/{TERMINAL_CSS_NAME}') == 1


_VENDOR_OWNED_NAMES = (
    "markdown_to_kramdown",
    "kramdown_to_markdown",
    "default_stylesheet",
    "alert_stylesheet",
    "html_to_markdown",
)


def test_lab_source_does_not_reimplement_vendor_converters() -> None:
    """Ownership split: Markdown↔HTML/CSS/Kramdown IAL stay in vendor/."""
    src_root = ROOT / "src" / "mcp_toolcall_lab"
    offenders: list[str] = []
    for path in src_root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        for name in _VENDOR_OWNED_NAMES:
            if f"def {name}" in text or f"function {name}" in text:
                offenders.append(f"{path.relative_to(ROOT)}:{name}")
    assert offenders == []


def test_pages_tree_excludes_raw_mcp_logs(tmp_path: Path) -> None:
    last = tmp_path / "last-run.json"
    last.write_text(
        json.dumps(
            {
                "turn": {
                    "case": "MCP_SUCCESS",
                    "assistant": "Exampleville",
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
        {"case": "MCP_SUCCESS", "assistant": "Exampleville 90001"}, cpu_llm_ok=True
    )
    assert ok["verdict"] == "PASS"
    assert ok["cpu_llm_ok"] is True
    miss = classify_stub_turn({"case": "MCP_UNREACHABLE", "assistant": "timed out"})
    assert miss["antipattern_id"] == MCP_UNREACHABLE
    down = classify_stub_turn(
        {"case": "MCP_SUCCESS", "assistant": "Exampleville"}, cpu_llm_ok=False
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
    assert "runtime-requirements" not in stub_df
    assert "--no-deps" in stub_df  # only the hash-pinned pip security bootstrap
    assert "pip @ https://files.pythonhosted.org/" in stub_df
    assert "vendor/markdown.py" in stub_df
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "actions/deploy-pages" not in workflow
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
    assert f"ghcr.io/ggml-org/llama.cpp:{provenance['tag']}@{digest}" in overlay
    assert "build: !reset" in overlay
    assert "environment: !reset" in overlay
    assert "/dev/tcp" not in overlay
    assert 'test: ["CMD", "curl", "-f", "http://127.0.0.1:8080/health"]' in overlay
    assert provenance["healthcheck"] == ["CMD", "curl", "-f", "http://localhost:8080/health"]
    model_pin = json.loads(
        (ROOT / "docker" / "stub-pages" / "models" / "model.gguf.provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert model_pin["sha256_verified_against_pin"] is True
    assert model_pin["sha256"] == "2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d"


def test_write_pages_does_not_embed_the_static_try_it_demo(tmp_path: Path) -> None:
    """Published Pages is generation + browser Wikipedia MCP #wiki + CI summary.

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
    assert PAGES_WIKI_JS_NAME in names
    assert f'src="{PAGES_HASH_JS_NAME}?v=' in html
    assert f'src="{PAGES_WIKI_JS_NAME}?v=' in html
    assert (out / PAGES_HASH_JS_NAME).read_text(encoding="utf-8") == (
        PAGES_HASH_JS_SOURCE.read_text(encoding="utf-8")
    )
    assert (out / PAGES_WIKI_JS_NAME).read_text(encoding="utf-8") == (
        PAGES_WIKI_JS_SOURCE.read_text(encoding="utf-8")
    )



def test_pixiv_pages_has_open_source_extract_workspace(tmp_path: Path) -> None:
    html = write_pages(tmp_path / "site", revision=_FAKE_REVISION).read_text(encoding="utf-8")
    assert 'data-testid="pages-pixiv-title"' in html
    assert 'data-testid="pages-pixiv-open-article"' in html
    assert 'data-testid="pages-pixiv-open-history"' in html
    assert 'data-testid="pages-pixiv-source-form"' in html
    assert 'data-testid="pages-pixiv-source-url"' in html
    assert 'data-testid="pages-pixiv-source-text"' in html
    assert 'data-testid="pages-pixiv-extract-submit"' in html
    assert 'data-testid="pages-pixiv-extract"' in html
    assert 'data-testid="pages-pixiv-error"' in html
    assert 'class="ui-output"' in html
    assert "pages-pixiv.js?v=" in html
    assert (tmp_path / "site" / "pages-pixiv.js").is_file()


def test_pixiv_pages_search_is_retired(tmp_path: Path) -> None:
    """This PR retires browser-side Search/fetch of dic.pixiv.net entirely --
    it is not a fix for the previous CORS-blocked Search flow (PR #58's
    ``pages-pixiv-fetch`` Search button / ``pages-pixiv-form`` /
    ``pages-pixiv-fallback`` contract). The public panel now only builds and
    validates the History -> Source -> Extract URLs and normalizes pasted
    source text; it never claims it can fetch dic.pixiv.net itself.
    """
    out = write_pages(tmp_path / "site", revision=_FAKE_REVISION).parent
    html = (out / "index.html").read_text(encoding="utf-8")
    js = (out / PAGES_PIXIV_JS_NAME).read_text(encoding="utf-8")
    pixiv_html = html[html.index('data-pages-view="pixiv"') :]
    assert 'data-testid="pages-pixiv-form"' not in pixiv_html
    assert 'data-testid="pages-pixiv-fetch"' not in pixiv_html
    assert 'data-testid="pages-pixiv-fallback"' not in pixiv_html
    assert ">Search<" not in pixiv_html
    assert "fetch(" not in js
    assert "loadArticle" not in js


def test_pixiv_pages_panel_never_regresses_to_localhost_dead_end(tmp_path: Path) -> None:
    """Blocking fix from PR #58: the public #pixiv panel must not tell the
    user to visit http://127.0.0.1:8765 as if that were reachable from
    GitHub Pages. This PR replaces the CORS-blocked browser fetch with a
    History -> Source -> Extract workflow instead, but the same guarantee
    holds: the local/MCP pointer is only ever a plain ``<pre>`` command
    block, never a clickable/actionable localhost URL.
    """
    out = write_pages(tmp_path / "site", revision=_FAKE_REVISION).parent
    html = (out / "index.html").read_text(encoding="utf-8")
    js = (out / PAGES_PIXIV_JS_NAME).read_text(encoding="utf-8")
    # Scope the index.html check to the #pixiv panel: the #wiki panel above it
    # legitimately prints "http://127.0.0.1:8765/wiki" as a local-run command.
    pixiv_html = html[html.index('data-pages-view="pixiv"') :]
    for surface in (pixiv_html, js):
        assert "MCP execution is local-only" not in surface
        assert "Local result URL" not in surface
        assert "http://127.0.0.1:8765" not in surface
    assert "dic.pixiv.net" in js
    assert PIXIV_LOCAL_SERVE.splitlines()[0] in pixiv_html
    assert (out / PAGES_PIXIV_JS_NAME).read_text(encoding="utf-8") == (
        PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    )


def test_render_pixiv_page_outputs_catalog_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_toolcall_lab.stub_front.dispatch_tool",
        lambda name, arguments: {
            "canonical_title": arguments["title"],
            "markdown": "# sample\\nbody",
            "headings": ["sample"],
            "source_url": "https://dic.pixiv.net/a/sample",
        },
    )
    html = render_pixiv_page(title="sample")
    assert 'data-testid="pixiv-result"' in html
    assert 'class="ui-output"' in html
    assert "canonical_title" in html
    assert "# sample" in html
    assert f'href="/{TERMINAL_CSS_NAME}"' in html


def test_local_wiki_uses_terminal_stylesheet() -> None:
    html = render_wiki_page()
    assert f'href="/{TERMINAL_CSS_NAME}"' in html
    assert '<main class="ui-page">' in html


def test_pixiv_page_response_success_reports_cache_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_toolcall_lab.stub_front.dispatch_tool",
        lambda name, arguments: {
            "canonical_title": arguments["title"],
            "markdown": "# sample",
            "headings": [],
            "source_url": "https://dic.pixiv.net/a/sample",
            "cache": "miss",
        },
    )
    status, body, headers = pixiv_page_response(title="sample")
    assert status == 200
    assert headers["X-Pixiv-Cache"] == "miss"
    assert "X-Pixiv-Stage" not in headers
    assert 'data-testid="pixiv-result"' in body


def test_pixiv_page_response_maps_upstream_http_403_to_bad_gateway(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError("blocked", stage="upstream_http", upstream_status=403)

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, body, headers = pixiv_page_response(title="NARUTO")
    assert status == 502
    assert headers["X-Pixiv-Stage"] == "upstream_http"
    assert headers["X-Pixiv-Upstream-Status"] == "403"
    assert 'data-testid="pixiv-error"' in body
    assert 'data-stage="upstream_http"' in body
    assert 'data-testid="pixiv-result"' not in body


def test_pixiv_page_response_maps_upstream_http_429_to_service_unavailable(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError("rate limited", stage="upstream_http", upstream_status=429)

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, _body, headers = pixiv_page_response(title="NARUTO")
    assert status == 503
    assert headers["X-Pixiv-Upstream-Status"] == "429"


def test_pixiv_page_response_maps_upstream_network_to_bad_gateway(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError("timed out", stage="upstream_network")

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, _body, headers = pixiv_page_response(title="NARUTO")
    assert status == 502
    assert headers["X-Pixiv-Stage"] == "upstream_network"
    assert "X-Pixiv-Upstream-Status" not in headers


def test_pixiv_page_response_maps_input_stage_to_bad_request(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError("article title is required", stage="input")

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, _body, headers = pixiv_page_response(title="anything")
    assert status == 400
    assert headers["X-Pixiv-Stage"] == "input"


def test_pixiv_page_response_maps_convert_error_to_internal_error(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError("bad markdown", stage="convert")

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, _body, headers = pixiv_page_response(title="NARUTO")
    assert status == 500
    assert headers["X-Pixiv-Stage"] == "convert"


def test_pixiv_page_response_maps_unexpected_exception_to_internal_error(monkeypatch) -> None:
    def boom(name, arguments):
        raise ValueError("boom")

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, body, headers = pixiv_page_response(title="NARUTO")
    assert status == 500
    assert headers["X-Pixiv-Stage"] == "internal"
    assert 'data-testid="pixiv-error"' in body
    assert 'data-testid="pixiv-result"' not in body


def test_pixiv_pages_tools_are_complete_catalog_entries() -> None:
    pixiv_tools = {
        "fetch_pixiv_dictionary_section",
        "fetch_pixiv_dictionary_article",
    }
    assert pixiv_tools <= set(AVAILABLE_TOOLS)
    assert pixiv_tools <= set(TOOL_DESCRIPTIONS)
    assert all(TOOL_DESCRIPTIONS[name].strip() for name in pixiv_tools)


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
    assert 'href="#pixiv"' in html
    assert 'data-pages-nav="pixiv"' in html
    assert 'data-testid="pages-nav-pixiv"' in html
    assert 'data-pages-view="pixiv"' in html
    assert 'data-testid="pages-pixiv"' in html
    assert "fetch_pixiv_dictionary_section" in html
    assert "fetch_pixiv_dictionary_article" in html
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
    assert "stays 404" in html[wiki_idx:]
    assert "http://127.0.0.1:8765/wiki" in html[wiki_idx:]
    assert PAGES_WIKI_BROWSER_NOTE in html[wiki_idx:]
    assert 'data-testid="pages-wiki-app"' in html[wiki_idx:]
    assert 'data-testid="pages-wiki-title"' in html[wiki_idx:]
    assert 'data-testid="pages-wiki-lang"' in html[wiki_idx:]
    assert 'data-testid="pages-wiki-fetch"' in html[wiki_idx:]
    assert 'data-testid="pages-wiki-heading"' in html[wiki_idx:]
    assert f'src="{PAGES_WIKI_JS_NAME}?v=' in html[wiki_idx:]
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
assert.strictEqual(pages.viewFromLocation("#pixiv", ""), "pixiv");
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
assert.strictEqual(pages.viewFromLocation("#pokemon", ""), "pokemon");
assert.strictEqual(pages.viewFromLocation("#pokemon?x=1", "?view=pokemon"), "pokemon");
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


def test_collect_revision_uses_checkout_head_with_actions_ref(monkeypatch) -> None:
    def fake_git(args: list[str]) -> str | None:
        table = {
            ("rev-parse", "HEAD"): "deadbeefcafebabe000000000000000000000000",
            ("rev-parse", "--short=8", "HEAD"): "deadbeef",
            ("branch", "--show-current"): "local-branch",
            ("show", "-s", "--format=%cI", "HEAD"): "2026-01-01T00:00:00+00:00",
            ("show", "-s", "--format=%s", "HEAD"): "local subject",
            ("status", "--porcelain"): " M stub_front.py",
        }
        return table.get(tuple(args))

    monkeypatch.setattr("mcp_toolcall_lab.stub_front._git_output", fake_git)
    meta = collect_revision(
        {
            "GITHUB_SHA": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "GITHUB_HEAD_REF": "feature/pr-head",
            "GITHUB_REF_NAME": "87/merge",
            "GITHUB_REPOSITORY": "myon-bioinformatics/mcp-toolcall-lab",
            "GITHUB_SERVER_URL": "https://github.com",
        }
    )
    assert meta["sha"] == "deadbeefcafebabe000000000000000000000000"
    assert meta["shortSha"] == "deadbeef"
    assert meta["ref"] == "feature/pr-head"
    assert meta["commitUrl"] == (
        "https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/"
        "deadbeefcafebabe000000000000000000000000"
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


def test_terminal_css_is_generated_from_pysaiyanweboo() -> None:
    """The committed stylesheet is scripts/build_terminal_css.py's output (pysaiyanweboo.core_css("terminal") + lab rules)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_terminal_css", ROOT / "scripts" / "build_terminal_css.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    import pysaiyanweboo
    assert module.TARGET == TERMINAL_CSS_SOURCE
    text = TERMINAL_CSS_SOURCE.read_text(encoding="utf-8")
    assert pysaiyanweboo.check_bundle(TERMINAL_CSS_SOURCE, **module.bundle_options()) == []
    assert text.startswith("/* web-ui-bundle/1 commit=")
    assert "/* css/themes/terminal.css */" in text and 'body[data-ui-theme="terminal"]' in text and ".term-workspace{" in text
    assert "cdn.jsdelivr.net" not in text
    galleria = module.GALLERIA.read_text(encoding="utf-8")
    assert galleria == module.render_galleria() and "#0a0e10" in galleria and "!important" in galleria


# --- Pokémon tab (#pokemon): web-ui's shared source panel + one JSON spec ---------------------------

POKEMON_FIXTURE = Path(__file__).parents[1] / "fixtures" / "pokeapi" / "ditto.json"


def test_write_pages_has_pokemon_tab_after_wiki_and_pixiv_and_ships_the_shared_source_panel(tmp_path: Path) -> None:
    out = tmp_path / "site"
    html = write_pages(out, revision=_FAKE_REVISION).read_text(encoding="utf-8")
    nav = [html.index(f'data-pages-nav="{name}"') for name in ("home", "wiki", "pixiv", "pokemon")]
    assert nav == sorted(nav)
    assert 'href="#pokemon"' in html and 'data-testid="pages-nav-pokemon"' in html
    assert 'id="pokemon" data-pages-view="pokemon" data-testid="pages-pokemon" hidden' in html
    pswb = load_pysaiyanweboo()
    assert pswb.source_panel_html(pokemon_spec()) in html                      # the panel is the shared one, built from the lab's spec
    assert f'src="{SOURCE_JS_NAME}?v=' in html
    assert (out / SOURCE_JS_NAME).read_text(encoding="utf-8") == pswb.js_source("source.js")
    assert pswb.css_source("source.css") in html
    assert not (out / "pages-pokemon.js").exists()                                  # no lab-owned panel script any more


def test_pokemon_spec_writes_the_python_adapter_markdown_byte_for_byte() -> None:
    from mcp_toolcall_lab.adapters.pokeapi import parse

    source = POKEMON_FIXTURE.read_text(encoding="utf-8")
    pswb = load_pysaiyanweboo()
    from_spec = pswb.source_markdown(pswb.source_extract(pokemon_spec(), source))
    assert from_spec == parse(source)["markdown"]
    assert from_spec.startswith("# ditto (#132)\n")


def test_pokemon_spec_is_the_panel_s_only_consumer_data() -> None:
    pswb = load_pysaiyanweboo()
    spec = pswb.source_spec(pokemon_spec())
    assert spec["id"] == "pokemon" and spec["url"] == "https://pokeapi.co/api/v2/pokemon/{name}"


# ═══ · stub pages smoke ══════════════════════════════════════════════════════════════════════════════════
# scripts/stub_pages_smoke.py -- no Docker, no network in default pytest.

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stub_pages_smoke as smoke  # noqa: E402


def test_cpu_completion_ok_reads_choices(monkeypatch) -> None:
    monkeypatch.setattr(
        smoke,
        "_post_json",
        lambda *a, **kw: (200, {"choices": [{"message": {"content": "pong"}}]}),
    )
    ok, detail = smoke._cpu_completion_ok()
    assert ok is True
    assert detail == ""


def test_cpu_completion_ok_false_on_non_200(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_post_json", lambda *a, **kw: (500, "boom"))
    ok, detail = smoke._cpu_completion_ok()
    assert ok is False
    assert "boom" in detail


def test_cpu_completion_ok_false_on_missing_choices(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_post_json", lambda *a, **kw: (200, {"choices": []}))
    ok, detail = smoke._cpu_completion_ok()
    assert ok is False
    assert "no choices" in detail


def test_default_backend_is_lite_stub() -> None:
    assert smoke.CPU_LLM_BACKEND == "lite-stub"


# ═══ · stub front ════════════════════════════════════════════════════════════════════════════════════════
# Stdlib stub front: heading → body, MCP cases, dual locators, chat_id.

def test_corpus_has_readme_and_wiki_headings() -> None:
    sections = load_corpus(DEFAULT_CORPUS)
    titles = {section.title for section in sections}
    assert "Find municipalities" in titles
    assert "Exampleville" in titles
    assert "Geography" in titles
    exampleville = lookup_heading("Exampleville", sections, fuzzy=False)
    assert exampleville is not None
    assert "90001" in exampleville.body


def test_exact_heading_wins_over_mcp_keyword() -> None:
    sections = parse_sections("# Exampleville\n\nFrom the corpus.\n")
    classified = classify_prompt("Exampleville", sections)
    assert classified["kind"] == "heading"
    assert classified["section"].body == "From the corpus."


def test_heading_select_is_on_the_composer_page() -> None:
    sections = load_corpus(DEFAULT_CORPUS)
    from mcp_toolcall_lab.stub_front import _page

    html = _page("chat_" + "c" * 24, [], sections=sections)
    assert 'data-testid="heading-select"' in html
    assert "Geography" in html
    assert "wikipedia.org" not in html
    assert "source-url" not in html


def test_sentence_with_exampleville_takes_mcp_path() -> None:
    sections = load_corpus(DEFAULT_CORPUS)
    classified = classify_prompt("Find municipalities named Exampleville", sections)
    assert classified["kind"] == "mcp"
    assert classified["tool"] == "find_municipalities"
    assert classified["arguments"]["query"] == "Exampleville"


def test_reply_heading_and_mcp_inprocess(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "mcp.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    heading = reply("# Find municipalities", chat_id="chat_" + "a" * 24)
    assert heading.case == CASE_HEADING_HIT
    assert heading.chat_id.startswith("chat_")
    assert "find_municipalities" in heading.assistant

    mcp_ok = reply("Where is Exampleville?", chat_id=heading.chat_id)
    assert mcp_ok.case == CASE_MCP_SUCCESS
    assert mcp_ok.tool == "find_municipalities"
    assert "90001" in mcp_ok.assistant
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["meta"]["chat_id"] == heading.chat_id
    assert row["meta"]["source"] == "stub-front"

    miss = reply("this heading does not exist xyz")
    assert miss.case == CASE_HEADING_MISS


def test_jsonrpc_error_is_not_classified_empty(monkeypatch) -> None:
    from mcp_toolcall_lab import stub_front

    class _Fake:
        def initialize(self) -> None:
            return None

        def list_tools(self) -> dict:
            return {"result": {"tools": []}}

        def call_tool(self, *args, **kwargs):
            return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}}

    monkeypatch.setattr(stub_front, "McpStdlibSession", lambda *args, **kwargs: _Fake())
    outcome, payload, via = stub_front._call_mcp(
        tool="find_municipalities",
        arguments={"query": "Exampleville"},
        chat_id="chat_x",
        call_id="call_x",
        mcp_url="http://127.0.0.1:9/mcp",
    )
    assert via == "mcp"
    assert outcome == "error"
    assert payload == {"code": -32603, "message": "boom"}


def test_stub_front_lists_tools_before_call(monkeypatch) -> None:
    from mcp_toolcall_lab import stub_front

    seen: list[str] = []

    class _Fake:
        def initialize(self) -> None:
            seen.append("initialize")

        def list_tools(self) -> dict:
            seen.append("list_tools")
            return {"result": {"tools": []}}

        def call_tool(self, *args, **kwargs):
            seen.append("call_tool")
            return {
                "result": {
                    "structuredContent": {"result": [{"code": "90001", "name": "Exampleville"}]}
                }
            }

    monkeypatch.setattr(stub_front, "McpStdlibSession", lambda *args, **kwargs: _Fake())
    outcome, payload, via = stub_front._call_mcp(
        tool="find_municipalities",
        arguments={"query": "Exampleville"},
        chat_id="chat_x",
        call_id="call_x",
        mcp_url="http://127.0.0.1:9/mcp",
    )
    assert via == "mcp"
    assert outcome == "success"
    assert seen == ["initialize", "list_tools", "call_tool"]
    assert payload[0]["name"] == "Exampleville"


def test_mcp_empty_case() -> None:
    sections = parse_sections("# Other\n\nbody\n")
    classified = classify_prompt("stations unknown code 00000", sections)
    assert classified["kind"] == "mcp"
    assert classified["arguments"]["municipality_code"] == "00000"
    turn = reply("stations unknown code 00000", sections=sections)
    assert turn.case == CASE_MCP_EMPTY
    assert turn.mcp_outcome == "empty"


def test_cli_turn_one_liner() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.stub_front", "turn", "--heading", "Exampleville", "--chat-id", "chat_" + "b" * 24],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["case"] == CASE_HEADING_HIT
    assert payload["chat_id"] == "chat_" + "b" * 24
    assert "90001" in payload["assistant"]


def test_html_exposes_librechat_and_owui_locators() -> None:
    state = StubState(sections=load_corpus(DEFAULT_CORPUS), mcp_url=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        with urlopen(base + "/", timeout=5) as response:
            assert response.status == 200
            url = response.geturl()
            html = response.read().decode("utf-8")
        assert "/c/chat_" in url
        assert f'data-testid="{LIBRECHAT_INPUT}"' in html
        assert f'data-testid="{LIBRECHAT_SEND}"' in html
        assert f'id="{OWUI_INPUT}"' in html
        assert f'id="{OWUI_SEND}"' in html
        chat_id = url.rstrip("/").rsplit("/", 1)[-1]
        body = f"prompt=Exampleville".encode("utf-8")
        request = Request(
            f"{base}/c/{chat_id}",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            page = response.read().decode("utf-8")
        assert f'id="{OWUI_RESPONSE}"' in page
        assert "90001" in page
        api = Request(
            f"{base}/api/turn",
            data=json.dumps({"prompt": "Find municipalities named Exampleville", "chat_id": chat_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(api, timeout=5) as response:
            turn = json.loads(response.read().decode("utf-8"))
        assert turn["case"] == CASE_MCP_SUCCESS
        assert turn["chat_id"] == chat_id
    finally:
        server.shutdown()
        server.server_close()


def test_main_turn_exit(capsys) -> None:
    code = main(["turn", "--heading", "Exampleville", "--chat-id", "chat_cli"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["case"] == CASE_HEADING_HIT


# ═══ · frontends ═════════════════════════════════════════════════════════════════════════════════════════

def test_both_clients_are_documented() -> None:
    assert set(PRODUCT_CLIENTS) == {"librechat", "openwebui"}
    assert set(REFERENCE_CLIENTS) == {"stub"}
    assert set(FRONTENDS) == PRODUCT_CLIENTS | REFERENCE_CLIENTS
    assert LIBRECHAT.composer.input.css() == '[data-testid="text-input"]'
    assert LIBRECHAT.composer.send.css() == '[data-testid="send-button"]'
    assert OPENWEBUI.composer.input.css() == "#chat-input"
    assert OPENWEBUI.composer.send.css() == "#send-message-button"
    assert OPENWEBUI.response.container is not None
    assert OPENWEBUI.response.container.css() == "#response-content-container"


def test_librechat_mcp_tool_key_and_compose_dns() -> None:
    assert LIBRECHAT.mcp.tool_key("find_municipalities") == "find_municipalities_mcp_lab"
    assert LIBRECHAT.mcp.compose_mcp_url == "http://mcp-mock:8000/mcp"
    assert OPENWEBUI.mcp.tool_key("find_municipalities") == "lab_find_municipalities"
    assert OPENWEBUI.mcp.compose_openai_url == "http://openai-mock:8090/v1"
    assert OPENWEBUI.compose_file == "docker/openwebui-smoke/docker-compose.yml"
    assert set(LIBRECHAT.advertised_tools) == set(AVAILABLE_TOOLS_frontends)


def test_aliases() -> None:
    assert get_frontend("owui").id == "openwebui"
    assert get_frontend("lc").id == "librechat"
    assert get_frontend("stubfront").id == "stub"
    assert STUB.role == "reference"
    assert STUB.composer.input.css() == LIBRECHAT.composer.input.css()


def test_catalog_and_describe_are_json() -> None:
    dumped = catalog()
    assert "librechat" in dumped["clients"]
    assert "openwebui" in dumped["clients"]
    assert "send_librechat" in dumped["one_liners"]
    assert "trace_probe" in dumped["one_liners"]
    assert "stub_front_turn" in dumped["one_liners"]
    assert "docker_logs" in dumped["one_liners"]
    assert dumped["roles"]["reference"] == ["stub"]
    summary = describe("librechat")
    assert summary["composer_input"] == '[data-testid="text-input"]'
    assert any("chat_ui send" in line for line in summary["one_liners"])
    assert any("trace_probe" in line for line in summary["one_liners"])


def test_frontends_module_one_liner() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.frontends", "openwebui"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["id"] == "openwebui"
    assert payload["composer"]["input"]["value"] == "chat-input"


def test_chat_ui_describe_one_liner() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.chat_ui", "describe"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert "one_liners" in payload
    assert payload["clients"]["librechat"]["default_url"].startswith("http://")


class _FakeLocator:
    def __init__(self, n: int) -> None:
        self._n = n

    def count(self) -> int:
        return self._n

    def wait_for(self, timeout=None) -> None:  # noqa: ANN001
        return None

    @property
    def last(self) -> "_FakeLocator":
        return self


class _FakePage:
    def __init__(self, n: int, last_text: str = "") -> None:
        self.n = n
        self.last_text = last_text
        self.calls: list[tuple] = []

    def locator(self, css: str) -> _FakeLocator:
        return _FakeLocator(self.n)

    def wait_for_function(self, expression, arg=None, timeout=None) -> None:  # noqa: ANN001
        self.calls.append((arg, timeout))
        if arg is None:
            return
        need = arg.get("n")
        if need is not None and self.n < need:
            raise TimeoutError("not enough assistant bubbles")
        needle = arg.get("text")
        if needle and needle not in self.last_text.lower():
            raise TimeoutError("last bubble missing fragment")


def test_wait_for_assistant_requires_a_new_bubble_when_min_count_set() -> None:
    page = _FakePage(n=1, last_text="hello")
    assert wait_for_assistant(page, OPENWEBUI, min_count=2) is False
    page.n = 2
    page.last_text = "Exampleville (code 90001)"
    assert (
        wait_for_assistant(
            page,
            OPENWEBUI,
            min_count=2,
            text_in_last="Exampleville",
        )
        is True
    )
    assert page.calls[-1][0]["n"] == 2
    assert page.calls[-1][0]["text"] == "exampleville"


# ═══ · librechat example ═════════════════════════════════════════════════════════════════════════════════
# Guard the LibreChat example config so the documented Streamable HTTP shape cannot drift.

EXAMPLE = ROOT / "examples" / "librechat.mcp.example.yaml"


def test_librechat_example_yaml_exists_and_targets_streamable_http() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "mcpServers:" in text
    assert "mcp-toolcall-lab:" in text
    assert "type: streamable-http" in text
    assert "url: http://127.0.0.1:8000/mcp" in text
    assert "requiresOAuth: false" in text
    assert "startup: true" in text
    # stdio-only knobs must not sneak into the HTTP example
    assert "command:" not in text
    assert "args:" not in text
    assert "librechat_mcp_mock.py" in text
    assert "openwebui_mcp_mock.py" not in text


def test_librechat_smoke_compose_uses_shared_docker_network() -> None:
    compose = (ROOT / "docker" / "librechat-smoke" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    yaml = (ROOT / "docker" / "librechat-smoke" / "librechat.yaml").read_text(encoding="utf-8")
    assert "name: mcp-toolcall-lab" in compose
    assert "mcp-mock:" in compose
    assert "openai-mock:" in compose
    assert "url: http://mcp-mock:8000/mcp" in yaml
    assert 'baseURL: "http://openai-mock:8090/v1"' in yaml
    assert "host.docker.internal" not in yaml
    dockerfile = (ROOT / "docker" / "librechat-smoke" / "Dockerfile.mcp").read_text(encoding="utf-8")
    assert "librechat_mcp_mock.py" in dockerfile
    assert "openwebui_mcp_mock.py" not in dockerfile
    openai_df = (ROOT / "docker" / "librechat-smoke" / "Dockerfile.openai").read_text(
        encoding="utf-8"
    )
    assert "src/mcp_toolcall_lab/mock/common.py" in openai_df
    assert "openai_toolcall_mock.py" in openai_df
    assert "server.py" not in openai_df


def test_librechat_docs_and_prompt_are_present() -> None:
    notes = (ROOT / "docs" / "librechat-mcp-notes.md").read_text(encoding="utf-8")
    assert "librechat_mcp_mock.py" in notes
    assert "http://mcp-mock:8000/mcp" in notes
    prompt = (ROOT / "system_prompts" / "strict_tool_selection_librechat.md").read_text(
        encoding="utf-8"
    )
    assert "LibreChat" in prompt
    assert "tools/list" in prompt


def test_librechat_smoke_workflow_tracks_the_librechat_named_mock() -> None:
    workflow = (ROOT / ".github" / "workflows" / "librechat-docker-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "librechat_mcp_mock.py" in workflow
    assert "mcp_toolcall_lab.docker_logs" in workflow
    assert "pytest -q" in workflow


# ═══ · markdown lib ══════════════════════════════════════════════════════════════════════════════════════
# markdown_lib.parse_sections -- inclusive nested section bodies.
#
# BLEACH-shaped corpus: an H2 parent (``あらすじ``) followed by two H3
# children, then a sibling H2. The parent's body must include both
# children's headings/text and stop only at the next H2 -- otherwise the
# Pages ``#wiki`` UI's "labels say inclusive but Docker/MCP say exclusive"
# parity gap (see issue #34) comes back for the Python side. Both the
# vendored ``markdown.py`` path and the local ATX-regex fallback must agree.

BLEACH_MARKDOWN = (
    "# BLEACH\n"
    "\n"
    "## あらすじ\n"
    "\n"
    "### 死神代行篇\n"
    "arc1 body\n"
    "\n"
    "### 尸魂界篇\n"
    "arc2 body\n"
    "\n"
    "## 登場人物\n"
    "people body\n"
)


def _assert_bleach_shape(sections) -> None:
    by_title = {section.title: section for section in sections}
    assert by_title.keys() >= {"あらすじ", "死神代行篇", "尸魂界篇", "登場人物"}

    lead = by_title["あらすじ"]
    assert lead.level == 2
    assert "### 死神代行篇" in lead.body
    assert "arc1 body" in lead.body
    assert "### 尸魂界篇" in lead.body
    assert "arc2 body" in lead.body
    assert "登場人物" not in lead.body
    assert "people body" not in lead.body

    arc1 = by_title["死神代行篇"]
    assert arc1.level == 3
    assert arc1.body == "arc1 body"

    people = by_title["登場人物"]
    assert people.level == 2
    assert people.body == "people body"


def test_parse_sections_inclusive_nested_bleach_shape_vendored() -> None:
    assert load_markdown() is not None, "vendor/markdown.py must be present for this check"
    _assert_bleach_shape(parse_sections_markdown_lib(BLEACH_MARKDOWN))


def test_parse_sections_inclusive_nested_bleach_shape_fallback(monkeypatch) -> None:
    import mcp_toolcall_lab.markdown_lib as markdown_lib

    monkeypatch.setattr(markdown_lib, "load_markdown", lambda: None)
    _assert_bleach_shape(parse_sections_markdown_lib(BLEACH_MARKDOWN))


def test_parse_sections_sibling_h2_still_stops_at_next_h2() -> None:
    text = "## A\nbody a\n\n## B\nbody b\n"
    sections = parse_sections_markdown_lib(text)
    assert [s.title for s in sections] == ["A", "B"]
    assert sections[0].body == "body a"
    assert sections[1].body == "body b"


def test_own_body_excludes_child_heading_prose_that_section_body_includes() -> None:
    """``own_body`` is the narrower, non-inclusive counterpart to ``Section.body``.

    Added for #60's Pixiv source-extraction "overview" field: a heading with
    no prose of its own before its first child heading -- e.g. ``あらすじ``,
    whose entire ``section.body`` belongs to its two H3 children -- must
    report an empty own body rather than leaking the children's text.
    """
    sections = parse_sections_markdown_lib(BLEACH_MARKDOWN)
    by_title = {section.title: section for section in sections}

    lead = by_title["あらすじ"]
    assert lead.body != ""
    assert own_body(BLEACH_MARKDOWN, lead) == ""

    people = by_title["登場人物"]
    assert own_body(BLEACH_MARKDOWN, people) == "people body"

    root = by_title["BLEACH"]
    assert own_body(BLEACH_MARKDOWN, root) == ""


def test_own_body_stops_at_first_child_heading_when_direct_prose_exists() -> None:
    text = "# Title\ndirect prose\n\n## Child\nchild body\n"
    sections = parse_sections_markdown_lib(text)
    root = next(s for s in sections if s.title == "Title")
    assert own_body(text, root) == "direct prose"


def test_own_body_falls_back_to_section_body_when_heading_line_not_found() -> None:
    """An out-of-sync ``Section`` whose title has no matching ``#`` line in
    the given Markdown gets ``section.body`` back, not an empty string or a
    crash.
    """
    sections = parse_sections_markdown_lib(BLEACH_MARKDOWN)
    lead = next(s for s in sections if s.title == "あらすじ")
    assert own_body("no headings in this string at all", lead) == lead.body


# ═══ · markdown nul ══════════════════════════════════════════════════════════════════════════════════════
# Upstream markdown#62 regressions through the lab's actual rendering path.

@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("keep \x00PH0\x00 here and `code`", "<p>keep \ufffdPH0\ufffd here and <code>code</code></p>\n"),
        ("only \x00PH0\x00 text", "<p>only \ufffdPH0\ufffd text</p>\n"),
        ("```\n\x00\n```", "<pre><code>\ufffd\n</code></pre>\n"),
    ],
)
def test_vendored_markdown_and_assistant_replace_input_nul(source, expected):
    md = load_markdown()
    assert md is not None
    for result in (md.markdown_to_html(source), _assistant_html(source)):
        assert result == expected
        assert "\x00" not in result


# ═══ · antipatterns ══════════════════════════════════════════════════════════════════════════════════════

CATALOG = Path(__file__).resolve().parents[1] / "fixtures" / "antipatterns" / "catalog.yaml"


def test_catalog_lists_every_known_id() -> None:
    text = CATALOG.read_text(encoding="utf-8")
    for antipattern_id in sorted(KNOWN_IDS):
        assert f"id: {antipattern_id}" in text, antipattern_id


def test_classify_pass_when_mcp_and_ui_agree() -> None:
    result = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=True,
        mcp_calls=[{"tool": "find_municipalities", "outcome": "success"}],
        ui_text="Exampleville (code 90001) is in Example Region.",
    )
    assert result["verdict"] == VERDICT_PASS
    assert result["antipattern_id"] is None


def test_classify_picker_off_and_mcp_not_called() -> None:
    picker = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=False,
        mcp_calls=[],
        ui_text="No MCP tools were attached",
    )
    assert picker["antipattern_id"] == MCP_PICKER_OFF

    missing = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=True,
        mcp_calls=[],
        ui_text="something else",
    )
    assert missing["antipattern_id"] == MCP_NOT_CALLED


def test_classify_auth_and_ui_gap() -> None:
    assert (
        classify_observation(
            input_found=False,
            send_clicked=False,
            logged_in=False,
            assistant_visible=False,
            openai_saw_tools=None,
            mcp_calls=[],
            ui_text="",
        )["antipattern_id"]
        == AUTH_BLOCKED
    )
    gap = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=True,
        mcp_calls=[{"tool": "find_municipalities", "outcome": "success"}],
        ui_text="an error occurred",
    )
    assert gap["antipattern_id"] == UI_NO_RESULT
    assert gap["verdict"] == VERDICT_ANTIPATTERN


def test_write_observation_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "observed.jsonl"
    write_observation(path, observation={"verdict": VERDICT_ANTIPATTERN, "antipattern_id": MCP_NOT_CALLED})
    write_observation(path, observation={"verdict": VERDICT_PASS, "antipattern_id": None})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "MCP_NOT_CALLED" in lines[0]
    assert "PASS" in lines[1]
