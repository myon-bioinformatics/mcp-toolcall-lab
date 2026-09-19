# Serverless stub try (GitHub Actions + Pages)

GitHub Pages hosts a **static** report. It cannot keep Docker running.
GitHub Actions is the compute: it starts one compose network, sends a
turn through the stub, appends anti-patterns as JSONL, then publishes
`_site/` to https://myon-bioinformatics.github.io/mcp-toolcall-lab/.

That published page also embeds a **client-side, JS-only demo** of the
heading → body path ("Try it (static, no MCP)") — see
[Static client-side demo](#static-client-side-demo-try-it-no-mcp) below.
It is a separate thing from the Actions summary: one runs in the
visitor's browser right now, the other is a snapshot of the last CI run.

`usable_session_id()` stays on the MCP mock: a missing session is
missing, never the string `"None"`.

## Same Docker network

| Service | DNS | Role |
| --- | --- | --- |
| `stub-front` | `:8765` | `markdown.py` + stdlib chat stub |
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
`index.html` + allowlisted `summary.json`.

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

## Static client-side demo ("Try it", no MCP)

Everything above needs Actions to actually run something. This is the
one piece that runs for a visitor with no CI, no Docker, nothing but the
static page: `src/mcp_toolcall_lab/static/stub_demo.js` re-implements
`stub_front.py`'s `slugify()` / `lookup_heading()` / `classify_prompt()`
in vanilla JS (no build step, no dependency), and `write_pages()` writes
its corpus (`{title, slug, body}` only — same public content as the
"Corpus headings" list, never `chat_id`/`_meta`/arguments) to
`stub-demo-data.json` alongside a copy of the script.

A heading `<select>` lists the same titles; picking one prints that
section body. No URL/source picker — the corpus is the vendored
fixtures, split by `markdown.py`. A heading prompt also still works. A prompt
that would trigger a real MCP tool call (mirrors `MCP_PATTERNS`' tokens
and tool names, kept in sync by
`tests/test_stub_pages.py::test_stub_demo_js_mirrors_mcp_patterns_tools_and_tokens`)
is **labelled, never faked** — this static page has no MCP server behind
it, so it says so instead of fabricating a result. The real round-trip
for that case is the "Last Actions summary" block, from the last
`stub-pages` Actions run. A `chat_<hex24>` id (same shape as
`record.py`'s `new_chat_id()`) is minted once per page load and shown
above the composer — display fidelity only, never sent anywhere (no
server here to send it to).

Composer/response element ids reuse `frontends.py`'s LibreChat/Open WebUI
locators (`#chat-input`, `#send-message-button`,
`#response-content-container`) for consistency with the rest of this
repo's dual-locator convention. `tests/test_stub_demo_browser.py` drives
a generated copy of this page with a real headless Chromium (same
`pytest.importorskip("playwright.sync_api")` + `browser-test` extra
pattern as `tests/test_browser_fetch_protocol.py`; skips cleanly in the
default `test` CI job, which does not install that extra) — heading hit,
MCP-pattern label, miss, and the chat_id being minted once and staying
stable across turns.

## Role split

**Cursor:** compose network, stdlib stub + vendored `markdown.py`,
Actions → Pages, anti-pattern JSONL, `usable_session_id` kept, lite
`cpu-llm` so the stack always starts.

**Claude:** real tiny GGUF overlay + auto-discovery/checksum fetch
script, opt-in workflow_dispatch wiring, a real `/v1/chat/completions`
smoke check (not just `/health`) for both backends, `cpu_llm_backend` in
the published summary, and the static client-side "Try it" demo above.
Accuracy still does not matter. No second log schema was added.
