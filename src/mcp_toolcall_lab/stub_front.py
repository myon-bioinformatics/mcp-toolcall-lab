"""Ultra-light chat stub: markdown corpus + stdlib HTTP, almost no deps.

LibreChat / Open WebUI stay the products under test. This module is a
**reference front** you can run with ``python -m mcp_toolcall_lab.stub_front``:

* Heading → body uses vendored ``markdown.py`` (``split_sections``) when
  present, else the local ATX splitter. Not a CommonMark engine.
* The same composer locators both products use (``data-testid=text-input`` /
  ``send-button`` and ``#chat-input`` / ``#send-message-button`` /
  ``#response-content-container``) so Playwright can point here without Docker.
* ``/c/{chat_id}`` mints and keeps a lab chat id, then puts it on MCP
  ``_meta`` *and* ``X-Chat-Id`` so the mock log is never an orphan row.
* Prompts that look like the lab tools (Yokohama / stations / prices) take
  the MCP path instead of heading lookup — the case split the real UIs hide.

No FastMCP / Playwright import on the serve path. MCP is optional urllib
JSON-RPC (``initialize`` → ``tools/list`` → ``tools/call``, the same
handshake Open WebUI and LibreChat use); without ``--mcp`` the catalog is
called in-process and still logged.

    python -m mcp_toolcall_lab.stub_front turn --heading "Yokohama"
    python -m mcp_toolcall_lab.stub_front serve --port 8765
    python -m mcp_toolcall_lab.stub_front serve --mcp http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS, TOOL_DESCRIPTIONS, dispatch_tool
from mcp_toolcall_lab.frontends import LIBRECHAT, OPENWEBUI, STUB
from mcp_toolcall_lab.markdown_lib import (
    Section,
    load_markdown,
    lookup_heading,
    parse_sections,
    slugify,
)
from mcp_toolcall_lab.mcp_http import McpStdlibSession
from mcp_toolcall_lab.pixiv_dictionary_tool import PixivDictionaryFetchError
from mcp_toolcall_lab.record import (
    OUTCOME_EMPTY,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    new_call_id,
    new_chat_id,
    record_call,
)
from mcp_toolcall_lab.wikipedia_tool import (
    DEFAULT_LANG,
    WikipediaFetchError,
    load_wikipedia_article,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "fixtures" / "stub_front"

CASE_HEADING_HIT = "HEADING_HIT"
CASE_HEADING_MISS = "HEADING_MISS"
CASE_MCP_SUCCESS = "MCP_SUCCESS"
CASE_MCP_EMPTY = "MCP_EMPTY"
CASE_MCP_ERROR = "MCP_ERROR"
CASE_MCP_UNREACHABLE = "MCP_UNREACHABLE"

WIKI_PAGES_DISCLAIMER = (
    "This form runs on the local stdlib stub (or a CI job), which fetches "
    "Wikipedia in-process (same path as the MCP tools). Pages #wiki is a "
    "separate browser → MediaWiki CORS form (no MCP) and cannot keep this "
    "/wiki backend online."
)
PAGES_WIKI_BROWSER_NOTE = (
    "Browser → MediaWiki API (not MCP). For the MCP Wikipedia tools / local "
    "stub form, run compose or stub_front serve."
)
WIKI_EXTRACT_NOTE = (
    "MediaWiki plaintext extract, HTML-escaped. "
    "This page does not scrape Wikipedia HTML or run it through markdown.py."
)

# Dual locators live in frontends.py — do not re-string them here.
LIBRECHAT_INPUT = LIBRECHAT.composer.input.value
LIBRECHAT_SEND = LIBRECHAT.composer.send.value
OWUI_INPUT = OPENWEBUI.composer.input.value
OWUI_SEND = OPENWEBUI.composer.send.value
OWUI_RESPONSE = OPENWEBUI.response.container.value if OPENWEBUI.response.container else "response-content-container"


@dataclass
class Turn:
    chat_id: str
    call_id: str
    case: str
    user: str
    assistant: str
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    mcp_outcome: str | None = None
    heading: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_corpus(root: Path | None = None) -> list[Section]:
    directory = root or DEFAULT_CORPUS
    sections: list[Section] = []
    if not directory.is_dir():
        return sections
    for path in sorted(directory.glob("*.md")):
        sections.extend(parse_sections(path.read_text(encoding="utf-8")))
    return sections


def _municipality_query(text: str) -> str:
    lowered = text.lower()
    if "yokohama" in lowered or "横浜" in text:
        return "Yokohama"
    if "matsudo" in lowered:
        return "Matsudo"
    if "chiyoda" in lowered or "tokyo" in lowered:
        return "Chiyoda"
    return text.strip()


def _station_args(text: str) -> dict[str, Any]:
    lowered = text.casefold()
    if "00000" in text or "unknown" in lowered:
        code = "00000"
    elif "yokohama" in lowered or "横浜" in text:
        code = "14109"
    elif "matsudo" in lowered:
        code = "12207"
    else:
        code = "13101"
    return {"municipality_code": code}


MCP_PATTERNS: tuple[tuple[tuple[str, ...], str, Any], ...] = (
    (("station", "駅", "find_stations"), "find_stations", _station_args),
    (
        ("price", "transaction", "価格", "find_transaction"),
        "find_transaction_prices",
        lambda _text: {"municipality_code": "14109", "year": 2025},
    ),
    (
        ("yokohama", "横浜", "municipalit", "市区町村", "find_municipalities"),
        "find_municipalities",
        lambda text: {"query": _municipality_query(text)},
    ),
)


def classify_prompt(prompt: str, sections: list[Section]) -> dict[str, Any]:
    """Exact heading wins (見出し→本文). Else MCP keywords. ``# Title`` forces heading."""
    text = prompt.strip()
    forced_heading = text.startswith("#")
    exact = lookup_heading(text, sections, fuzzy=False)
    if exact is not None:
        return {"kind": "heading", "section": exact}
    lowered = text.casefold()
    if not forced_heading:
        for tokens, tool, args_fn in MCP_PATTERNS:
            if any(token in lowered for token in tokens):
                return {"kind": "mcp", "tool": tool, "arguments": args_fn(text)}
    section = lookup_heading(text, sections, fuzzy=True)
    if section:
        return {"kind": "heading", "section": section}
    return {"kind": "miss"}


def render_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_empty list (valid call, no rows)_"
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(str(key))
    table_rows = [[str(row.get(key, "")) for key in keys] for row in rows]
    md = load_markdown()
    if md is not None and hasattr(md, "table"):
        return md.table(keys, table_rows)
    # Last-resort pipe table when vendor/markdown.py is absent. Generation
    # and HTML/CSS for GFM tables belong to the vendored library.
    header = "| " + " | ".join(keys) + " |"
    sep = "| " + " | ".join("---" for _ in keys) + " |"
    body = ["| " + " | ".join(str(row.get(key, "")) for key in keys) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _inprocess_tool(tool: str, arguments: dict[str, Any]) -> tuple[str, Any]:
    try:
        result = dispatch_tool(tool, arguments)
    except KeyError:
        return OUTCOME_ERROR, f"unknown tool {tool}"
    return (OUTCOME_EMPTY if result == [] else OUTCOME_SUCCESS), result


def _call_mcp(
    *,
    tool: str,
    arguments: dict[str, Any],
    chat_id: str,
    call_id: str,
    mcp_url: str | None,
) -> tuple[str, Any, str]:
    meta = {"chat_id": chat_id, "call_id": call_id, "source": "stub-front"}
    if not mcp_url:
        outcome, payload = _inprocess_tool(tool, arguments)
        record_call(tool=tool, arguments=arguments, outcome=outcome, result=payload, meta=meta)
        return outcome, payload, "inprocess"
    try:
        session = McpStdlibSession(mcp_url, client_name="stub-front")
        session.initialize()
        session.list_tools()
        body = session.call_tool(tool, arguments, meta=meta, extra_headers={"X-Chat-Id": chat_id})
        if isinstance(body, dict) and body.get("error"):
            return OUTCOME_ERROR, body["error"], "mcp"
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            return OUTCOME_ERROR, body, "mcp"
        if result.get("isError"):
            return OUTCOME_ERROR, result, "mcp"
        structured = result.get("structuredContent") or {}
        payload = structured.get("result", structured)
        if payload == [] or payload == {}:
            return OUTCOME_EMPTY, payload, "mcp"
        return OUTCOME_SUCCESS, payload, "mcp"
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return OUTCOME_ERROR, str(exc), "mcp"


def reply(
    prompt: str,
    *,
    chat_id: str | None = None,
    sections: list[Section] | None = None,
    mcp_url: str | None = None,
    corpus: Path | None = None,
) -> Turn:
    chat_id = chat_id or new_chat_id()
    call_id = new_call_id()
    sections = sections if sections is not None else load_corpus(corpus)
    classified = classify_prompt(prompt, sections)
    if classified["kind"] == "heading":
        section: Section = classified["section"]
        return Turn(
            chat_id=chat_id,
            call_id=call_id,
            case=CASE_HEADING_HIT,
            user=prompt,
            assistant=section.body or f"_(no body under {section.title})_",
            heading=section.title,
            notes=f"ATX heading L{section.line}",
        )
    if classified["kind"] == "miss":
        titles = ", ".join(section.title for section in sections[:12]) or "(empty corpus)"
        return Turn(
            chat_id=chat_id,
            call_id=call_id,
            case=CASE_HEADING_MISS,
            user=prompt,
            assistant=f"No heading matched. Known: {titles}",
            notes="heading lookup miss",
        )

    tool = str(classified["tool"])
    arguments = dict(classified["arguments"])
    outcome, payload, via = _call_mcp(
        tool=tool, arguments=arguments, chat_id=chat_id, call_id=call_id, mcp_url=mcp_url
    )
    if via == "mcp" and outcome == OUTCOME_ERROR and isinstance(payload, str):
        case = CASE_MCP_UNREACHABLE if "urlopen" in payload or "timed out" in payload else CASE_MCP_ERROR
        assistant = f"MCP {case}: {payload}"
    elif outcome == OUTCOME_ERROR:
        case = CASE_MCP_ERROR
        assistant = f"MCP error: {payload}"
    elif outcome == OUTCOME_EMPTY:
        case = CASE_MCP_EMPTY
        assistant = render_rows([])
    else:
        case = CASE_MCP_SUCCESS
        assistant = render_rows(payload) if isinstance(payload, list) else json.dumps(payload, ensure_ascii=False)
    return Turn(
        chat_id=chat_id,
        call_id=call_id,
        case=case,
        user=prompt,
        assistant=assistant,
        tool=tool,
        arguments=arguments,
        mcp_outcome=outcome,
        notes=via,
    )


def _assistant_html(text: str) -> str:
    md = load_markdown()
    if md is not None and hasattr(md, "markdown_to_html"):
        return md.markdown_to_html(text)
    return f"<pre>{html.escape(text)}</pre>"


def _vendor_style_tag(md: Any = None) -> str:
    """Embed vendor CSS for ``markdown_to_html`` output.

    ``default_stylesheet()`` lives in ``vendor/markdown.py`` (tables, alerts,
    code, footnotes, …). The lab does not keep a second showcase stylesheet.
    """
    module = md if md is not None else load_markdown()
    if module is not None and hasattr(module, "default_stylesheet"):
        return f"<style>{module.default_stylesheet()}</style>"
    return ""


PAGES_FORBIDDEN_NAMES = frozenset(
    {
        "mcp-toolcalls.jsonl",
        "openai-mock.jsonl",
        "cpu-llm.jsonl",
        "antipatterns.jsonl",
        "last-run.json",
    }
)

# Local-only heading → body demo (tests / write_stub_demo_page). Not published
# on GitHub Pages — that report is generation identity + #wiki MediaWiki UI.
STATIC_DIR = Path(__file__).resolve().parent / "static"
STUB_DEMO_JS_SOURCE = STATIC_DIR / "stub_demo.js"
STUB_DEMO_JS_NAME = "stub-demo.js"
STUB_DEMO_DATA_NAME = "stub-demo-data.json"
PAGES_HASH_JS_SOURCE = STATIC_DIR / "pages_hash.js"
PAGES_HASH_JS_NAME = "pages-hash.js"
PAGES_WIKI_JS_SOURCE = STATIC_DIR / "pages_wiki.js"
PAGES_WIKI_JS_NAME = "pages-wiki.js"
PAGES_PIXIV_JS_SOURCE = STATIC_DIR / "pages_pixiv.js"
PAGES_PIXIV_JS_NAME = "pages-pixiv.js"

# The lab's one small CSS surface (terminal-lite). Replaces the five-link
# web-ui CDN chain on Pages and the local /wiki and /pixiv surfaces; web-ui
# itself is unchanged. Served locally at /terminal.css, copied next to the
# published index.html by write_pages().
TERMINAL_CSS_SOURCE = STATIC_DIR / "terminal.css"
TERMINAL_CSS_NAME = "terminal.css"


def _terminal_css_link(*, href: str = TERMINAL_CSS_NAME, cache_bust: str = "") -> str:
    """The lab's single page-shell stylesheet -- no web-ui CDN chain."""
    return f'<link rel="stylesheet" href="{href}{cache_bust}">'


def _asset_cache_bust(path: Path) -> str:
    """``?v=<sha256[:10]>`` of ``path``'s current bytes.

    The published ``index.html`` and the ``<script src>`` it points at are
    written from the same ``write_pages()`` call, so a content-derived query
    string always matches the JS actually deployed alongside it -- a CDN or
    browser cache from a previous revision cannot serve stale JS under an
    unchanged URL.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    return f"?v={digest}"


PAGES_WIKI_SERVE = (
    "python -m mcp_toolcall_lab.stub_front serve --port 8765\n"
    "# then open /wiki  (http://127.0.0.1:8765/wiki)"
)
BUILD_META_NAME = "build_meta.json"
BUILD_META_KEYS = (
    "version",
    "sha",
    "shortSha",
    "ref",
    "committedAt",
    "subject",
    "commitUrl",
    "dirty",
)

# Public Pages may only show these keys. Prompts, arguments, _meta, and ids stay off-site.
PAGES_SUMMARY_KEYS = (
    "source",
    "verdict",
    "antipattern_id",
    "case",
    "cpu_llm_ok",
    "cpu_llm_backend",
    "cpu_llm_completion_ok",
    "stub_ok",
    "turn_http",
    "showed_expected_fragment",
    "observation_n",
    "antipattern_ids",
)


def pages_summary(
    last_run: dict[str, Any] | None = None,
    observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Allowlisted public summary. No prompts, arguments, `_meta`, or raw ids."""
    last_run = last_run or {}
    observations = observations or []
    turn = last_run.get("turn") if isinstance(last_run.get("turn"), dict) else {}
    obs = last_run.get("observation") if isinstance(last_run.get("observation"), dict) else {}
    health = last_run.get("stub_health") if isinstance(last_run.get("stub_health"), dict) else {}
    ids = sorted(
        {
            str(row["antipattern_id"])
            for row in observations
            if isinstance(row, dict) and row.get("antipattern_id")
        }
    )
    if obs.get("antipattern_id") and str(obs["antipattern_id"]) not in ids:
        ids.append(str(obs["antipattern_id"]))
        ids.sort()
    assistant = str(turn.get("assistant") or "")
    return {
        "source": "stub-pages",
        "verdict": obs.get("verdict"),
        "antipattern_id": obs.get("antipattern_id"),
        "case": turn.get("case") or obs.get("case"),
        "cpu_llm_ok": last_run.get("cpu_llm_ok"),
        "cpu_llm_backend": last_run.get("cpu_llm_backend"),
        "cpu_llm_completion_ok": last_run.get("cpu_llm_completion_ok"),
        "stub_ok": health.get("status") == 200,
        "turn_http": last_run.get("turn_http"),
        "showed_expected_fragment": "yokohama" in assistant.lower(),
        "observation_n": len(observations),
        "antipattern_ids": ids,
    }


PAGES_OUTPUT_NAME = "_site"


def _git_output(args: list[str]) -> str | None:
    """Run a git command in the repo root. None if git is missing or the command fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _porcelain_relpath(line: str) -> str | None:
    """Return the worktree-relative path from one ``git status --porcelain`` line."""
    if len(line) < 4:
        return None
    rest = line[3:]
    if " -> " in rest:
        rest = rest.split(" -> ", 1)[1]
    rest = rest.strip()
    if len(rest) >= 2 and rest[0] == rest[-1] == '"':
        rest = rest[1:-1]
    rest = rest.replace("\\", "/").rstrip("/")
    return rest or None


def _ignore_roots(ignore_paths: Sequence[Path | str] = ()) -> list[Path]:
    roots = [REPO_ROOT / PAGES_OUTPUT_NAME]
    for path in ignore_paths:
        candidate = Path(path)
        roots.append(candidate if candidate.is_absolute() else REPO_ROOT / candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _relpath_is_ignored(rel: str, roots: Sequence[Path]) -> bool:
    repo = REPO_ROOT.resolve()
    for root in roots:
        try:
            rel_root = os.path.relpath(root.resolve(), repo).replace("\\", "/")
        except (OSError, ValueError):
            continue
        if rel_root.startswith("..") or rel_root in {".", ""}:
            continue
        rel_root = rel_root.rstrip("/")
        if rel == rel_root or rel.startswith(rel_root + "/"):
            return True
    return False


def _status_is_dirty(status: str | None, ignore_paths: Sequence[Path | str] = ()) -> bool:
    """True when porcelain reports a change outside ``_site/`` and ``ignore_paths``."""
    if not status:
        return False
    roots = _ignore_roots(ignore_paths)
    for line in status.splitlines():
        rel = _porcelain_relpath(line)
        if rel is None:
            continue
        if _relpath_is_ignored(rel, roots):
            continue
        return True
    return False


def collect_revision(
    env: Mapping[str, str] | None = None,
    *,
    ignore_paths: Sequence[Path | str] = (),
) -> dict[str, Any]:
    """Collect a small revision/generation block for the Pages report.

    Schema written to ``_site/build_meta.json`` (same idea as
    ``myon-bioinformatics/markdown``'s Pages report / flutter_navigation_basic's
    ``build_meta``, flat — this report has no pubspec / artifact-size fields):

    - ``version``: ``mcp_toolcall_lab.__version__`` if that attribute exists,
      else null. Do not invent a version string from ``pyproject.toml``.
    - ``sha`` / ``shortSha`` (8): ``GITHUB_SHA`` when set, else ``git rev-parse``.
    - ``ref``: ``GITHUB_REF_NAME`` when set, else ``git branch --show-current``.
    - ``committedAt`` / ``subject`` / ``dirty``: git (``%cI``, ``%s``, porcelain).
      ``dirty`` ignores the default ``_site/`` tree and ``ignore_paths`` so the
      generated Pages output cannot mark a clean source tree dirty.
    - ``commitUrl``: ``{server}/{repo}/commit/{sha}`` when sha and
      ``GITHUB_REPOSITORY`` are known (``GITHUB_SERVER_URL`` or https://github.com).
    """
    import mcp_toolcall_lab as lab

    environ = os.environ if env is None else env

    def _env(name: str) -> str | None:
        value = (environ.get(name) or "").strip()
        return value or None

    github_sha = _env("GITHUB_SHA")
    sha = github_sha or _git_output(["rev-parse", "HEAD"])
    if sha:
        short_sha = sha[:8]
    else:
        short_sha = _git_output(["rev-parse", "--short=8", "HEAD"])

    ref = _env("GITHUB_REF_NAME") or _git_output(["branch", "--show-current"])
    committed_at = _git_output(["show", "-s", "--format=%cI", "HEAD"])
    subject = _git_output(["show", "-s", "--format=%s", "HEAD"])
    status = _git_output(["status", "--porcelain"])
    dirty = _status_is_dirty(status, ignore_paths)

    repo = _env("GITHUB_REPOSITORY")
    server = (_env("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    commit_url = f"{server}/{repo}/commit/{sha}" if sha and repo else None

    version = getattr(lab, "__version__", None)
    if version is not None:
        version = str(version)

    return {
        "version": version,
        "sha": sha,
        "shortSha": short_sha,
        "ref": ref,
        "committedAt": committed_at,
        "subject": subject,
        "commitUrl": commit_url,
        "dirty": dirty,
    }


def _revision_html(revision: Mapping[str, Any]) -> str:
    parts: list[str] = []
    version = revision.get("version")
    if version:
        parts.append(f"Version {html.escape(str(version))}")

    short = revision.get("shortSha") or "unknown"
    sha = revision.get("sha")
    title = f' title="{html.escape(str(sha))}"' if sha else ""
    commit_url = revision.get("commitUrl")
    label = f"Commit {html.escape(str(short))}"
    if commit_url:
        parts.append(f'<a href="{html.escape(str(commit_url))}"{title}>{label}</a>')
    else:
        parts.append(f"<span{title}>{label}</span>")

    if revision.get("ref"):
        parts.append(html.escape(str(revision["ref"])))
    if revision.get("committedAt"):
        parts.append(html.escape(str(revision["committedAt"])))
    if revision.get("subject"):
        parts.append(html.escape(str(revision["subject"])))
    if revision.get("dirty"):
        parts.append("(dirty)")

    return f'<p id="build-meta">{" · ".join(parts)}</p>'


def _pages_nav_html() -> str:
    """Always-visible Home / #wiki / #pixiv controls. Hash only — Pages stays one endpoint."""
    return (
        '<p id="pages-nav">'
        '<a href="#" data-pages-nav="home" data-testid="pages-nav-home">Home</a>'
        " · "
        '<a href="#wiki" data-pages-nav="wiki" data-testid="pages-nav-wiki">'
        "Wiki</a>"
        " · "
        '<a href="#pixiv" data-pages-nav="pixiv" data-testid="pages-nav-pixiv">Pixiv</a>'
        "</p>"
    )


def _pages_panel_html(*, view: str, inner: str, hidden: bool = False) -> str:
    hid = " hidden" if hidden else ""
    safe = html.escape(view)
    return (
        f'<section id="{safe}" data-pages-view="{safe}" '
        f'data-testid="pages-{safe}"{hid}>{inner}</section>'
    )


def _pages_wiki_back_html() -> str:
    return (
        '<p><a href="#" data-pages-nav="home" data-testid="pages-nav-back">'
        "Back to report</a></p>"
    )


def _pages_home_markdown(md: Any, summary_text: str) -> str:
    return "\n".join(
        [
            "This host's [Wiki](#wiki) panel fetches Wikipedia from the browser "
            "(MediaWiki Action API, CORS `origin=*`, no MCP). The MCP tools and "
            "local `GET /wiki` form still need compose or `stub_front serve`.",
            "",
            md.heading("Last Actions summary", 2),
            "Allowlisted snapshot from the last `stub-pages` GitHub Actions run "
            "(stub + MCP mock + CPU-class model on one compose network). "
            "Raw MCP logs are not on Pages. This host cannot keep Docker running.",
            md.code_block(summary_text, lang="json"),
            md.bullet_list(
                [
                    "Local: `docker compose -f docker/stub-pages/docker-compose.yml up --build`",
                    "Actions: workflow `stub-pages` (`workflow_dispatch`)",
                    "MCP: `http://mcp-mock:8000/mcp` · CPU model: `http://cpu-llm:8080/v1`",
                ]
            ),
        ]
    )


def _pages_wiki_markdown(md: Any) -> str:
    return md.section(
        "Wikipedia extract (browser → MediaWiki API)",
        [
            "This `#wiki` panel calls Wikipedia's Action API from the browser "
            "(`origin=*`). It is not an MCP tool call. github.io `/wiki` stays 404; "
            "that path is the local stdlib stub. MCP tools / local form:",
            md.code_block(PAGES_WIKI_SERVE, lang="bash"),
            "CI screenshots of the local stub still use "
            "`fixtures/wikipedia/yokohama_extract.json`. A heading switch on this "
            "panel reuses the in-memory extract (no second fetch).",
        ],
    )


def _pages_wiki_app_html(*, cache_bust: str = "") -> str:
    """Raw HTML for the browser MediaWiki form. Kept outside markdown.py."""
    return (
        f'<p data-testid="pages-wiki-disclaimer">{html.escape(PAGES_WIKI_BROWSER_NOTE)}</p>'
        '<div id="pages-wiki-app" data-testid="pages-wiki-app">'
        '<form id="pages-wiki-form" data-testid="pages-wiki-form">'
        "<p>"
        '<label for="pages-wiki-title">Wikipedia title</label> '
        '<input id="pages-wiki-title" name="title" value="Yokohama" '
        'placeholder="Article title" data-testid="pages-wiki-title">'
        "</p>"
        "<p>"
        '<label for="pages-wiki-lang">Language</label> '
        '<select id="pages-wiki-lang" name="lang" data-testid="pages-wiki-lang">'
        '<option value="en" selected>en</option>'
        '<option value="ja">ja</option>'
        "</select> "
        '<button type="submit" data-testid="pages-wiki-fetch">Fetch</button>'
        "</p>"
        "<p>"
        '<label for="pages-wiki-heading">Heading</label> '
        '<select id="pages-wiki-heading" name="heading" data-testid="pages-wiki-heading">'
        '<option value="">(full extract)</option>'
        "</select>"
        "</p>"
        "</form>"
        '<p data-testid="pages-wiki-error" hidden></p>'
        '<p data-testid="pages-wiki-canonical" hidden></p>'
        f'<p data-testid="pages-wiki-extract-note">{html.escape(WIKI_EXTRACT_NOTE)}</p>'
        '<pre class="term-output" data-testid="pages-wiki-extract" hidden></pre>'
        "</div>"
        f'<script src="{PAGES_WIKI_JS_NAME}{cache_bust}"></script>'
    )


def _stub_demo_html() -> str:
    """Raw HTML for the static, client-side heading-lookup demo.

    Built outside the markdown pipeline (unlike the rest of this page) so a
    <script>/<div> is never at risk of being escaped by a markdown-to-HTML
    pass that treats raw HTML as plain text -- vendor/markdown.py is "stdlib
    helpers, not a CommonMark engine" and makes no promise either way.
    """
    locator_ids = {
        "input": OWUI_INPUT,
        "inputTestId": LIBRECHAT_INPUT,
        "send": OWUI_SEND,
        "sendTestId": LIBRECHAT_SEND,
        "response": OWUI_RESPONSE,
    }
    return (
        '<h2 id="stub-demo-heading">Try it (static, no MCP)</h2>'
        "<p>Heading → body lookup only, running entirely in your browser "
        "(no server, no Docker, no MCP call) — same logic as "
        "<code>stub_front.py</code>'s <code>classify_prompt()</code>, "
        "re-implemented in vanilla JS. A prompt that would trigger a real MCP "
        "tool call is labelled, never faked; the actual round-trip is the "
        '"Last Actions summary" below.</p>'
        '<div id="stub-demo" data-testid="stub-demo"></div>'
        f'<script src="{STUB_DEMO_JS_NAME}"></script>'
        "<script>\n"
        "window.mcpToolcallLabStubDemo.mount(\n"
        "  document.getElementById('stub-demo'),\n"
        f"  {json.dumps(STUB_DEMO_DATA_NAME)},\n"
        f"  {json.dumps(locator_ids)}\n"
        ");\n"
        "</script>"
    )


def _load_json_object(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_stub_demo_page(
    out_dir: Path,
    *,
    corpus: Path | None = None,
) -> Path:
    """Write the local heading-lookup demo. Not used by the published Pages tree."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sections = load_corpus(corpus)
    demo_data = [{"title": s.title, "slug": s.slug, "body": s.body} for s in sections]
    (out_dir / STUB_DEMO_DATA_NAME).write_text(
        json.dumps(demo_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / STUB_DEMO_JS_NAME).write_text(STUB_DEMO_JS_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    html_page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>stub-front local heading demo</title>"
        f"</head><body>{_stub_demo_html()}</body></html>\n"
    )
    index = out_dir / "index.html"
    index.write_text(html_page, encoding="utf-8")
    return index


def write_pages(
    out_dir: Path,
    *,
    last_run: Path | None = None,
    observations: Path | None = None,
    corpus: Path | None = None,
    revision: Mapping[str, Any] | None = None,
) -> Path:
    """Write a static GitHub Pages tree. Raw MCP/debug logs stay off this tree.

    ``corpus`` is accepted so ``pages --corpus`` still parses; mock headings are
    not listed on the published index (they read as a Wiki TOC).

    The published ``index.html`` stays on one origin/endpoint. ``#wiki`` (and
    optional ``?view=wiki``) is an in-page view switch, not a github.io
    ``/wiki`` path (that URL stays 404). The ``#wiki`` panel fetches
    MediaWiki from the browser. Local Docker ``GET /wiki`` is unchanged.
    """
    from mcp_toolcall_lab.mock.common import read_jsonl

    # Snapshot revision before mkdir/write so first generation does not create
    # untracked files first. Ignore ``_site/`` and this out_dir so a leftover
    # or regenerated tree cannot mark a clean source checkout dirty.
    meta = dict(revision) if revision is not None else collect_revision(ignore_paths=(out_dir,))
    md = load_markdown()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in PAGES_FORBIDDEN_NAMES:
        leftover = out_dir / name
        if leftover.exists():
            leftover.unlink()
    summary = pages_summary(_load_json_object(last_run), read_jsonl(observations) if observations else [])
    summary_text = json.dumps(summary, indent=2, ensure_ascii=False)
    if md is not None:
        home_inner = md.markdown_to_html(_pages_home_markdown(md, summary_text))
        wiki_inner = md.markdown_to_html(_pages_wiki_markdown(md))
    else:
        home_inner = "<pre>" + html.escape(summary_text) + "</pre>"
        wiki_inner = (
            "<h2>Wikipedia extract (browser → MediaWiki API)</h2>"
            f"<p>{html.escape(PAGES_WIKI_BROWSER_NOTE)}</p>"
            "<pre>" + html.escape(PAGES_WIKI_SERVE) + "</pre>"
        )
    wiki_inner = wiki_inner + _pages_wiki_app_html(cache_bust=_asset_cache_bust(PAGES_WIKI_JS_SOURCE))
    pixiv_inner = (
        "<h2>Pixiv Encyclopedia search</h2>"
        "<p>Pixiv Encyclopedia fetching stays MCP/local-server owned. GitHub Pages provides "
        "the same search/result workspace without pretending that a static host can run MCP.</p>"
        '<div id="pages-pixiv-app" data-testid="pages-pixiv-app">'
        '<form id="pages-pixiv-form" data-testid="pages-pixiv-form">'
        '<p><label for="pages-pixiv-title">Pixiv Encyclopedia title</label> '
        '<input id="pages-pixiv-title" name="title" value="" placeholder="Article title" '
        'data-testid="pages-pixiv-title"> '
        '<button type="submit" data-testid="pages-pixiv-fetch">Search</button></p>'
        "</form>"
        '<p class="term-muted" data-testid="pages-pixiv-status">Run the local stub to execute the MCP-backed search.</p>'
        '<pre class="term-output" data-testid="pages-pixiv-extract" hidden></pre>'
        "</div>"
        "<ul>"
        f"<li><code>fetch_pixiv_dictionary_section</code> — {html.escape(TOOL_DESCRIPTIONS['fetch_pixiv_dictionary_section'])}</li>"
        f"<li><code>fetch_pixiv_dictionary_article</code> — {html.escape(TOOL_DESCRIPTIONS['fetch_pixiv_dictionary_article'])}</li>"
        "</ul>"
        '<pre class="term-output">python -m mcp_toolcall_lab.stub_front serve --port 8765</pre>'
        f'<script src="{PAGES_PIXIV_JS_NAME}{_asset_cache_bust(PAGES_PIXIV_JS_SOURCE)}"></script>'
    )
    # Hide/show uses the HTML hidden attribute. Converted Markdown CSS comes
    # from vendor/markdown.py (default_stylesheet), not a lab-authored copy.
    # The page shell itself is styled by the single lab-owned terminal.css --
    # no web-ui CDN chain.
    html_page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"{_vendor_style_tag(md)}"
        f"{_terminal_css_link(cache_bust=_asset_cache_bust(TERMINAL_CSS_SOURCE))}"
        "<title>mcp-toolcall-lab stub</title>"
        '</head><body><main class="term-page term-shell">'
        '<header class="term-header"><div><h1 class="term-title">mcp-toolcall-lab stub</h1>'
        '<p class="term-muted">Static Pages evidence for MCP tool-call experiments.</p></div>'
        f'<div class="term-meta"><span class="term-tag">mcp</span><span class="term-tag">{len(AVAILABLE_TOOLS)} tools</span></div></header>'
        f"{_pages_nav_html()}"
        '<div class="term-workspace"><section class="term-panel term-result">'
        f"{_pages_panel_html(view='home', inner=_revision_html(meta) + home_inner)}"
        f"{_pages_panel_html(view='wiki', inner=wiki_inner + _pages_wiki_back_html(), hidden=True)}"
        f"{_pages_panel_html(view='pixiv', inner=pixiv_inner + _pages_wiki_back_html(), hidden=True)}"
        '</section><aside aria-label="Supporting information">'
        '<section class="term-card term-evidence"><h2>Tool catalog</h2>'
        '<ul id="tool-catalog">' + "".join(
            f'<li><code>{html.escape(name)}</code> — {html.escape(TOOL_DESCRIPTIONS[name])}</li>'
            for name in AVAILABLE_TOOLS
        ) + '</ul></section>'
        '<section class="term-card term-history"><h2>Workspace provenance</h2>'
        f'<p>Styling: single lab-owned <code>{TERMINAL_CSS_NAME}</code> (no external stylesheet chain).</p></section>'
        '</aside></div></main>'
        f'<script src="{PAGES_HASH_JS_NAME}{_asset_cache_bust(PAGES_HASH_JS_SOURCE)}"></script>'
        "</body></html>\n"
    )
    (out_dir / "index.html").write_text(html_page, encoding="utf-8")
    (out_dir / TERMINAL_CSS_NAME).write_text(
        TERMINAL_CSS_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out_dir / PAGES_HASH_JS_NAME).write_text(
        PAGES_HASH_JS_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out_dir / PAGES_WIKI_JS_NAME).write_text(
        PAGES_WIKI_JS_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out_dir / PAGES_PIXIV_JS_NAME).write_text(
        PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(summary_text + "\n", encoding="utf-8")
    (out_dir / BUILD_META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out_dir / "index.html"


def _heading_option_label(section: Section) -> str:
    """``## Title`` display label -- same ATX prefix as pages_wiki.js's
    ``headingLabel()`` (#33), so the local Docker ``/wiki`` form and the
    published Pages ``#wiki`` panel show matching option text. The
    ``<option>`` value stays the bare title; only the label gets the prefix.
    """
    level = section.level if 1 <= section.level <= 6 else 2
    return f"{'#' * level} {section.title}"



# Stage -> HTTP status for a failed /pixiv fetch. "upstream_http" is not
# listed here because its status depends on the upstream code it carries
# (see _pixiv_status_for): a 429/503 from dic.pixiv.net is retriable (503),
# anything else upstream (403 included) is a bad-gateway (502).
PIXIV_STAGE_STATUS = {
    "input": 400,
    "upstream_network": 502,
    "convert": 500,
    "internal": 500,
}


def _pixiv_status_for(stage: str, upstream_status: int | None) -> int:
    if stage == "upstream_http":
        return 503 if upstream_status in (429, 503) else 502
    return PIXIV_STAGE_STATUS.get(stage, 500)


def _pixiv_page_html(*, title: str, error: str, stage: str, payload: str) -> str:
    if error:
        body = f'<p role="alert" data-testid="pixiv-error" data-stage="{html.escape(stage)}">{html.escape(error)}</p>'
    else:
        body = f'<pre class="term-output" data-testid="pixiv-result">{html.escape(payload)}</pre>' if payload else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'{_terminal_css_link(href="/" + TERMINAL_CSS_NAME)}'
        '<title>Pixiv Encyclopedia search</title></head>'
        '<body><main class="term-page"><h1 class="term-title">Pixiv Encyclopedia search</h1>'
        '<form method="get" action="/pixiv"><label for="pixiv-title">Title</label> '
        f'<input class="term-input" id="pixiv-title" name="title" value="{html.escape(title)}"> '
        '<button class="term-button" type="submit">Search</button></form>'
        + body
        + '</main></body></html>'
    )


def pixiv_page_response(*, title: str = "") -> tuple[int, str, dict[str, str]]:
    """Server-rendered Pixiv Encyclopedia page plus the real fetch status/headers.

    A caller that only wants the HTML (the pre-existing ``render_pixiv_page``
    contract) can ignore the status/headers; ``/pixiv`` uses all three so a
    blocked or rate-limited upstream is visible on the wire instead of being
    silently flattened into an HTTP 200.
    """
    title = title.strip()
    error = ""
    stage = ""
    upstream_status: int | None = None
    cache: str | None = None
    result: Any = None
    status = 200
    if title:
        try:
            result = dispatch_tool("fetch_pixiv_dictionary_article", {"title": title})
            if isinstance(result, dict):
                cache = result.get("cache")
        except PixivDictionaryFetchError as exc:
            error = str(exc)
            stage = exc.stage
            upstream_status = exc.upstream_status
            status = _pixiv_status_for(stage, upstream_status)
        except Exception as exc:  # unexpected failure outside the adapter's own error type
            error = str(exc)
            stage = "internal"
            status = 500
    payload = ""
    if result is not None:
        payload = json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result
    body = _pixiv_page_html(title=title, error=error, stage=stage, payload=payload)
    headers: dict[str, str] = {}
    if error:
        headers["X-Pixiv-Stage"] = stage
        if upstream_status is not None:
            headers["X-Pixiv-Upstream-Status"] = str(upstream_status)
    else:
        headers["X-Pixiv-Cache"] = cache or "unknown"
    return status, body, headers


def _pixiv_error_from_body(body: str) -> str | None:
    """Extract the already-escaped Pixiv error for internal diagnostics only."""
    match = re.search(r'data-testid="pixiv-error"[^>]*>([^<]*)</p>', body)
    return html.unescape(match.group(1)) if match else None


def render_pixiv_page(*, title: str = "") -> str:
    """Compatibility wrapper over ``pixiv_page_response()`` -- HTML only."""
    _, body, _ = pixiv_page_response(title=title)
    return body

def render_wiki_page(
    *,
    title: str = "",
    heading: str = "",
    lang: str = DEFAULT_LANG,
) -> str:
    """Server-rendered Wikipedia title form + heading select. No live JS backend."""
    title = title.strip()
    heading = heading.strip()
    lang = (lang or DEFAULT_LANG).strip() or DEFAULT_LANG
    error = ""
    extract = ""
    canonical = ""
    cache_status = ""
    section_title = ""
    section_body = ""
    section_options = ['<option value="">(full extract)</option>']
    if title:
        try:
            article, cache_status = load_wikipedia_article(title, lang=lang)
            canonical = article.canonical_title
            extract = article.extract
            for section in article.sections():
                selected = " selected" if heading and section.title == heading else ""
                section_options.append(
                    f'<option value="{html.escape(section.title)}"{selected}>'
                    f"{html.escape(_heading_option_label(section))}</option>"
                )
            if heading:
                match = lookup_heading(heading, article.sections(), fuzzy=True)
                if match is not None:
                    section_title = match.title
                    section_body = match.body
                    # Re-mark the matched title as selected when the query was fuzzy.
                    if match.title != heading:
                        section_options = ['<option value="">(full extract)</option>']
                        for section in article.sections():
                            selected = " selected" if section.title == match.title else ""
                            section_options.append(
                                f'<option value="{html.escape(section.title)}"{selected}>'
                                f"{html.escape(_heading_option_label(section))}</option>"
                            )
        except WikipediaFetchError as exc:
            error = str(exc)
            cache_status = "error"

    heading_select = ""
    if extract:
        heading_select = (
            '<label for="wiki-heading-select">Heading</label>'
            f'<select id="wiki-heading-select" name="heading" data-testid="wiki-heading-select">'
            f"{''.join(section_options)}</select>"
            '<button type="submit" data-testid="wiki-show-section">Show section</button>'
        )

    article_html = ""
    if canonical:
        article_html += (
            f'<p>canonical title <strong data-testid="wiki-canonical-title">'
            f"{html.escape(canonical)}</strong></p>"
        )
    if cache_status:
        article_html += (
            f'<p data-testid="wiki-cache" data-cache="{html.escape(cache_status)}">'
            f"cache {html.escape(cache_status)}</p>"
        )
    if error:
        article_html += f'<p data-testid="wiki-error">{html.escape(error)}</p>'
    if extract:
        article_html += (
            f"<p>{html.escape(WIKI_EXTRACT_NOTE)}</p>"
            f'<pre class="term-output" data-testid="wiki-extract">{html.escape(extract)}</pre>'
        )
    if section_title:
        article_html += (
            f"<h2>Selected section: {html.escape(section_title)}</h2>"
            f'<pre class="term-output" data-testid="wiki-section">{html.escape(section_body)}</pre>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>wikipedia article (local stub)</title>
{_terminal_css_link(href="/" + TERMINAL_CSS_NAME)}
</head>
<body><main class="term-page">
<p><a href="/">chat stub</a> · Wikipedia article (this server)</p>
<p class="term-muted">{html.escape(WIKI_PAGES_DISCLAIMER)}</p>
<form method="get" action="/wiki">
<label for="wiki-title">Wikipedia title</label>
<input id="wiki-title" name="title" value="{html.escape(title)}" data-testid="wiki-title">
<label for="wiki-lang">Language</label>
<input id="wiki-lang" name="lang" value="{html.escape(lang)}" data-testid="wiki-lang" size="4">
<button type="submit" data-testid="wiki-fetch">Show article</button>
{heading_select}
</form>
{article_html}
</main></body></html>
"""


def _page(chat_id: str, turns: list[Turn], prompt: str = "", sections: list[Section] | None = None) -> str:
    bubbles = []
    for turn in turns:
        bubbles.append(
            f'<section class="turn" data-case="{html.escape(turn.case)}">'
            f"<h3>user</h3><pre>{html.escape(turn.user)}</pre>"
            f"<h3>assistant · {html.escape(turn.case)}</h3>"
            f'<article id="{OWUI_RESPONSE}">{_assistant_html(turn.assistant)}</article>'
            f"</section>"
        )
    thread = "\n".join(bubbles) or "<p>Send a heading (e.g. <code>Find municipalities</code>) or <code>Yokohama</code>.</p>"
    options = ['<option value="">(heading)</option>']
    for section in sections or []:
        options.append(f'<option value="{html.escape(section.title)}">{html.escape(section.title)}</option>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>stub-front {html.escape(chat_id)}</title>
{_vendor_style_tag()}
{_terminal_css_link(href="/" + TERMINAL_CSS_NAME)}
</head>
<body class="term-page">
<p>lab chat_id <code data-testid="chat-id">{html.escape(chat_id)}</code> · stdlib stub
 · <a href="/wiki">Wikipedia article (this server only)</a></p>
<form method="post" action="/c/{html.escape(chat_id)}">
<label for="heading-select">Headings</label>
<select id="heading-select" name="heading" data-testid="heading-select">{"".join(options)}</select>
<textarea name="prompt" data-testid="{LIBRECHAT_INPUT}" id="{OWUI_INPUT}">{html.escape(prompt)}</textarea>
<button type="submit" data-testid="{LIBRECHAT_SEND}" id="{OWUI_SEND}">Send</button>
</form>
{thread}
</body></html>
"""


@dataclass
class StubState:
    sections: list[Section]
    mcp_url: str | None
    chats: dict[str, list[Turn]] = field(default_factory=dict)


def make_handler(state: StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _chat_id_from_path(self) -> str | None:
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            if len(parts) >= 2 and parts[0] == "c" and parts[1] not in {"new"}:
                return parts[1]
            return None

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/health":
                self._send(200, b'{"ok":true}\n', "application/json")
                return
            if path == "/" + TERMINAL_CSS_NAME:
                self._send(200, TERMINAL_CSS_SOURCE.read_bytes(), "text/css; charset=utf-8")
                return
            if path == "/pixiv":
                query = parse_qs(parsed.query)
                title = (query.get("title") or [""])[0]
                start = time.monotonic()
                status, page, extra_headers = pixiv_page_response(title=title)
                elapsed_ms = round((time.monotonic() - start) * 1000, 1)
                upstream_status = extra_headers.get("X-Pixiv-Upstream-Status")
                print(
                    json.dumps(
                        {
                            "msg": "pixiv request",
                            "title": title,
                            "status": status,
                            "stage": extra_headers.get("X-Pixiv-Stage"),
                            "upstream_status": int(upstream_status) if upstream_status else None,
                            "error": _pixiv_error_from_body(page),
                            "cache": extra_headers.get("X-Pixiv-Cache"),
                            "elapsed_ms": elapsed_ms,
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                self._send(status, page.encode("utf-8"), "text/html; charset=utf-8", extra_headers)
                return
            if path == "/wiki":
                query = parse_qs(parsed.query)
                page = render_wiki_page(
                    title=(query.get("title") or [""])[0],
                    heading=(query.get("heading") or [""])[0],
                    lang=(query.get("lang") or [DEFAULT_LANG])[0],
                )
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path in {"/", "/login", "/c", "/c/new"}:
                chat_id = new_chat_id()
                self.send_response(302)
                self.send_header("Location", f"/c/{chat_id}")
                self.end_headers()
                return
            chat_id = self._chat_id_from_path()
            if chat_id:
                state.chats.setdefault(chat_id, [])
                page = _page(chat_id, state.chats[chat_id], sections=state.sections)
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(404, b"not found\n", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            path = urlparse(self.path).path
            prompt = ""
            if self.headers.get("Content-Type", "").startswith("application/json"):
                try:
                    payload = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    self._send(400, b'{"error":"invalid json"}\n', "application/json")
                    return
                prompt = str(payload.get("prompt") or payload.get("heading") or "")
                chat_id = str(payload.get("chat_id") or self._chat_id_from_path() or new_chat_id())
            else:
                form = parse_qs(raw.decode("utf-8"))
                prompt = (form.get("prompt") or [""])[0] or (form.get("heading") or [""])[0]
                chat_id = self._chat_id_from_path() or new_chat_id()
            turn = reply(prompt, chat_id=chat_id, sections=state.sections, mcp_url=state.mcp_url)
            state.chats.setdefault(chat_id, []).append(turn)
            if path.startswith("/api/"):
                self._send(
                    200,
                    (json.dumps(turn.as_dict(), ensure_ascii=False) + "\n").encode("utf-8"),
                    "application/json",
                )
                return
            self._send(
                200,
                _page(chat_id, state.chats[chat_id], sections=state.sections).encode("utf-8"),
                "text/html; charset=utf-8",
            )

    return Handler


def serve(host: str, port: int, *, corpus: Path, mcp_url: str | None) -> None:
    state = StubState(sections=load_corpus(corpus), mcp_url=mcp_url)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"stub-front http://{host}:{port}/  corpus={corpus} mcp={mcp_url or 'inprocess'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mcp_toolcall_lab.stub_front")
    sub = parser.add_subparsers(dest="cmd")
    turn = sub.add_parser("turn", help="one heading/MCP turn as JSON (no server)")
    turn.add_argument("--heading", "--prompt", dest="prompt", default="", required=True)
    turn.add_argument("--chat-id", default="")
    turn.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    turn.add_argument("--mcp", default=os.environ.get("STUB_MCP_URL", ""))
    serve_p = sub.add_parser("serve", help="stdlib HTTP composer")
    serve_p.add_argument("--host", default=os.environ.get("STUB_HOST", urlparse(STUB.default_url).hostname or "127.0.0.1"))
    serve_p.add_argument("--port", type=int, default=int(os.environ.get("STUB_PORT", str(urlparse(STUB.default_url).port or 8765))))
    serve_p.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    serve_p.add_argument("--mcp", default=os.environ.get("STUB_MCP_URL", ""))
    pages = sub.add_parser("pages", help="write a static GitHub Pages tree")
    pages.add_argument("--out", default="_site")
    pages.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    pages.add_argument("--last-run", default=os.environ.get("STUB_LAST_RUN", ""))
    pages.add_argument("--observations", default=os.environ.get("ANTIPATTERN_LOG", ""))
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 2
    if args.cmd == "pages":
        path = write_pages(
            Path(args.out),
            last_run=Path(args.last_run) if args.last_run else None,
            observations=Path(args.observations) if args.observations else None,
            corpus=Path(args.corpus),
        )
        print(path)
        return 0
    if args.cmd == "turn":
        result = reply(
            args.prompt,
            chat_id=args.chat_id or None,
            corpus=Path(args.corpus),
            mcp_url=args.mcp or None,
        )
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return 0 if result.case != CASE_HEADING_MISS else 1
    serve(args.host, args.port, corpus=Path(args.corpus), mcp_url=args.mcp or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
