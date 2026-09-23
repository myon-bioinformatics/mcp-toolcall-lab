# Serverless stub try (GitHub Actions + Pages)

GitHub Pages hosts a **static** report. It cannot keep Docker running
or the local `GET /wiki` stub backend. It **can** fetch Wikipedia from
the browser: the MediaWiki Action API allows CORS via `origin=*`.
GitHub Actions is the compute for the Last Actions snapshot: it starts
one compose network, sends a turn through the stub, appends
anti-patterns as JSONL, then publishes `_site/` to
https://myon-bioinformatics.github.io/mcp-toolcall-lab/.

The published `index.html` stays on one GitHub Pages endpoint. A
same-origin hash switch (`#wiki`, optional `?view=wiki`) shows an
in-page browser → MediaWiki form; there is no live `/wiki` path on
github.io (that URL stays 404 by design — it is the local stub).
Default view (`#` / empty hash) is generation identity (Commit /
optional Version, plus `_site/build_meta.json`), a short pointer, and a
concise Last Actions snapshot. It does **not** embed the mock
heading-pulldown "Try it" demo — that pulldown read
`fixtures/stub_front`, not Wikipedia, and looked like a stub Wiki UI.

`usable_session_id()` stays on the MCP mock: a missing session is
missing, never the string `"None"`.

## Same Docker network

| Service | DNS | Role |
| --- | --- | --- |
| `stub-front` | `:8765` | vendored `markdown.py` + stdlib chat stub |
| `mcp-mock` | `http://mcp-mock:8000/mcp` | generated FastMCP mock |
| `cpu-llm` | `http://cpu-llm:8080/v1` | CPU-class model (lite stand-in today) |

```bash
docker compose -f docker/stub-pages/docker-compose.yml up --build
python scripts/stub_pages_smoke.py
python -m mcp_toolcall_lab.stub_front pages --out _site --last-run test-results/last-run.json
```

Actions: workflow `stub-pages` (`workflow_dispatch`). Default `pytest -q`
does not start this stack.

Ledger: `test-results/antipatterns.jsonl` (dictionary:
`fixtures/antipatterns/catalog.yaml`). A miss does not pretend Send failed.
Raw MCP / cpu-llm / last-run files stay Actions artifacts. Pages only gets
`index.html`, `pages-hash.js`, `pages-wiki.js`, allowlisted `summary.json`,
and `build_meta.json`. Docker
container logs are captured into `test-results/docker-logs/` on the smoke
job; they are artifacts, not Pages content.

## Real tiny GGUF (opt-in)

`docker-compose.gguf.yml` swaps `cpu-llm` for a **digest-pinned**
`ghcr.io/ggml-org/llama.cpp:server` (`b11046`, see
`llama.cpp.image.provenance.json`) serving a real model instead of
`demos/cpu_llm_lite.py`. Healthcheck is the image's own `curl -f /health`
(no `bash` / `/dev/tcp`).
Off by default (never on a plain push); turn it on via the `stub-pages`
workflow's `use_real_gguf` dispatch input, or locally:

```bash
scripts/fetch_tiny_cpu_gguf.sh docker/stub-pages/models/model.gguf
docker compose -f docker/stub-pages/docker-compose.yml \
  -f docker/stub-pages/docker-compose.gguf.yml up --build
```

`scripts/fetch_tiny_cpu_gguf.py` uses a **commit-pinned URL + sha256** for
the default file (`bartowski/SmolLM2-135M-Instruct-GGUF` /
`SmolLM2-135M-Instruct-Q4_K_M.gguf` @ `09816acd…`,
`sha256=2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d`).
That digest was hashed on GitHub-hosted Actions run
[35433174462](https://github.com/myon-bioinformatics/mcp-toolcall-lab/actions/runs/35433174462)
after download. A custom `CPU_LLM_GGUF_REPO` still discovers via the HF
API; `CPU_LLM_GGUF_URL` downloads that URL. Bytes are always re-hashed;
a pin mismatch is a hard failure. Overlay `build: !reset` drops the lite
Dockerfile so `up --build` pulls the digest-pinned llama.cpp image
instead of trying to tag a built stand-in as `image@sha256:...`.
Pages deploy runs only from `main` (`github.ref == 'refs/heads/main'`).
A feature-branch `workflow_dispatch` (including `use_real_gguf=true`)
must not enter the `github-pages` environment.

`scripts/stub_pages_smoke.py` also calls `/v1/chat/completions` for real
(not just `/health`) — a process can be "up" while inference itself is
broken (OOM, bad model args, a GGUF that never finished loading). A miss
there is logged as `CPU_LLM_COMPLETION_FAILED`, distinct from
`CPU_LLM_UNREACHABLE`. `cpu_llm_backend` (`lite-stub` / `real-gguf`) is
in the published summary so the Pages report says honestly which one ran.

## Shared web-ui workspace

The published Pages shell uses the shared `myon-bioinformatics/web-ui` v1
tool workspace pinned to
`e7d16a2ce0cee76b7744a4b6a8f8ce374491a4db`. This intentionally keeps the
newer shared `.ui-output` readability styling while applying the same Modern
theme contract (`themes/modern.css` + `body[data-ui-theme="modern"]`) across
Pages, local `/pixiv`, and local `/wiki`. The wide layout keeps the primary report/wiki view beside supporting
tool-catalog/provenance cards; web-ui's `720px` fallback stacks the primary
content first on narrow screens.

The supporting catalog is generated directly from Python's `AVAILABLE_TOOLS`
and `TOOL_DESCRIPTIONS`, so Pages exposes the current seven tools, including
`fetch_pixiv_dictionary_section` and `fetch_pixiv_dictionary_article`,
without maintaining a second hand-written list. MCP protocol behavior,
fixtures, trace/log semantics, and execution remain owned by this repository;
web-ui supplies presentation only.

## What the published page is

Converted Markdown on this page (headings, lists, fenced JSON, …) is
styled with `vendor/markdown.py`'s `default_stylesheet()`, not a
lab-authored showcase CSS file. Hide/show of Home vs `#wiki` still uses
the HTML `hidden` attribute.

Near the top: a plain-HTML generation block (`Commit <shortSha>`, linked
when `commitUrl` is known; Version only if `mcp_toolcall_lab.__version__`
exists — this package does not invent one). Schema:
`write_pages()` → `_site/build_meta.json` (`version`, `sha`, `shortSha`,
`ref`, `committedAt`, `subject`, `commitUrl`, `dirty`). Prefers
`GITHUB_SHA` / `GITHUB_REF_NAME` / `GITHUB_REPOSITORY` /
`GITHUB_SERVER_URL` when Actions sets them. `dirty` is source-tree dirty:
it ignores default `_site/` and the chosen `--out` directory so generating
(or regenerating) the Pages tree cannot mark a clean checkout dirty.

The home view keeps Last Actions on this host. `#wiki` is a browser
MediaWiki client (title → headings → section, in-memory extract on
heading switch). MCP `fetch_wikipedia_*` and the local stub form stay
on compose / `stub_front serve`.

```bash
python -m mcp_toolcall_lab.stub_front serve --port 8765
# open http://127.0.0.1:8765/wiki
```

Deep-link the wiki panel as
`https://myon-bioinformatics.github.io/mcp-toolcall-lab/#wiki`
(optional `#wiki?title=Yokohama&lang=ja`; or `index.html?view=wiki`,
which the hash script treats like `#wiki`). `write_pages()` copies
`pages-hash.js` and `pages-wiki.js` next to `index.html`. Do not
confuse that with `stub-demo.js` (local/test only).

Pages `#wiki` is CORS MediaWiki from the browser, not MCP. The "Last
Actions summary" JSON on the home view is the last `stub-pages` smoke
snapshot — the actual value of this host — kept thinner than generation
+ the `#wiki` form.

## Local heading-lookup JS (not on Pages)

`src/mcp_toolcall_lab/static/stub_demo.js` still re-implements
`slugify()` / `lookup_heading()` / `classify_prompt()` in vanilla JS for
local tests. `write_stub_demo_page()` writes that script plus
`{title, slug, body}` corpus JSON. `write_pages()` does **not** copy
those files into `_site/` and does not mount `#stub-demo` on the
published index. The Pages `#wiki` view is the browser MediaWiki form
(plus a pointer to local `GET /wiki` / MCP), not that fixtures/stub_front
mock.

A prompt that would trigger a real MCP tool call (mirrors
`MCP_PATTERNS`' tokens and tool names, kept in sync by
`tests/test_stub_pages.py::test_stub_demo_js_mirrors_mcp_patterns_tools_and_tokens`)
is **labelled, never faked**. `tests/test_stub_demo_browser.py` drives
`write_stub_demo_page()` with headless Chromium (same
`pytest.importorskip("playwright.sync_api")` + `browser-test` extra
pattern as `tests/test_browser_fetch_protocol.py`; skips in default
`pytest -q`).

## Role split

**Cursor:** compose network, stdlib stub + vendored `markdown.py`,
Actions → Pages, anti-pattern JSONL, `usable_session_id` kept, lite
`cpu-llm` so the stack always starts.

**Claude:** real tiny GGUF overlay + auto-discovery/checksum fetch
script, opt-in workflow_dispatch wiring, a real `/v1/chat/completions`
smoke check (not just `/health`) for both backends, and
`cpu_llm_backend` in the published summary. Accuracy still does not
matter. No second log schema was added.


## Terminal-lite single-CSS prototype

The next UI experiment is intentionally scoped to this repository. The goal is to replace the multi-file web-ui theme chain with one small lab-owned stylesheet, while keeping semantic HTML, readable output, and desktop/iPhone screenshot regression coverage. `ascii_artist` may enhance text presentation when available but is not a required UI dependency. Shared `web-ui` is deliberately unchanged; reusable pieces can be reverse-imported only after this prototype proves useful.
