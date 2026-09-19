# mcp-toolcall-lab

Reproducible experiments for reliable LLM-to-MCP tool discovery, initialization, and calls using FastMCP and mock APIs.

This repository deliberately does **not** call the Ministry of Land, Infrastructure, Transport and Tourism (MLIT) Real Estate Information Library API. It is a safe mock target for checking whether a model calls only tools actually advertised by an MCP server.

## What is in the tree

- A FastMCP Streamable HTTP server with three deterministic real-estate-style mock tools.
- A copyable standalone file generated from the package so tool names, schemas, and docstrings cannot drift.
- LibreChat / Open WebUI as clients under test; a stdlib markdown stub as a reference front.
- Protocol tests (curl / httpx / SDK) plus chat/direct tracing through one JSONL log.

## Requirements

- Python 3.11 or newer (`datetime.UTC` and `X | Y` type hints).
- `fastmcp==3.4.7` (pinned). FastMCP 4.x requires `mcp>=2` and cannot share a venv with Open WebUI's `mcp==1.27.2`.

## Run locally

`openwebui_mcp_mock.py` and `librechat_mcp_mock.py` are both generated from `src/mcp_toolcall_lab/` — the exact same mock server, copied twice under product-matched names so each README/doc can point at a file named for what you're actually running, not a name from a different product. Copy whichever one matches your client onto another machine; do not copy only one function. After changing tools, regenerate both with `python -m mcp_toolcall_lab.export`.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python openwebui_mcp_mock.py
```

If your shell does not support the activation command, invoke `.venv/bin/python` directly.

On a VM or container that should only run the mock, copy the standalone file and install FastMCP:

```bash
pip install "fastmcp==3.4.7"
MCP_HOST=0.0.0.0 python openwebui_mcp_mock.py
```

The MCP endpoint is Streamable HTTP at `/mcp`. In Open WebUI (v0.6.31+), add an **MCP (Streamable HTTP)** connection, not OpenAPI:

| Where Open WebUI runs | Server URL |
| --- | --- |
| Same host as the mock | `http://127.0.0.1:8000/mcp` |
| Docker, mock on the host | `http://host.docker.internal:8000/mcp` |
| Another VM / compose service | `http://<mock-hostname>:8000/mcp` |

The mock process must bind `MCP_HOST=0.0.0.0` whenever the client is not on the same network namespace. Set auth to **None** unless you add a token yourself.

### LibreChat (second chat client, same mock)

LibreChat is the next first-class UI target. Point it at the **same** `/mcp` server via
`librechat.yaml` — do not stand up a second mock. Run the LibreChat-named copy of the exact
same generated server:

```bash
python librechat_mcp_mock.py
```

Then copy [`examples/librechat.mcp.example.yaml`](examples/librechat.mcp.example.yaml) into
your LibreChat config and use the chat MCP picker (Agents are a follow-up axis).

```yaml
mcpServers:
  mcp-toolcall-lab:
    type: streamable-http
    url: http://127.0.0.1:8000/mcp
    requiresOAuth: false
    startup: true
```

Details: [`docs/librechat_mcp_notes.md`](docs/librechat_mcp_notes.md).
Prompt: [`system_prompts/strict_tool_selection_librechat.md`](system_prompts/strict_tool_selection_librechat.md).

### LibreChat in Docker + Playwright (input → Send → MCP record)

`docker/librechat-smoke/` starts published LibreChat, the MCP mock, and the
OpenAI tool-call mock on **one Docker network** (`mcp-toolcall-lab`). LibreChat
reaches them by service DNS (`http://mcp-mock:8000/mcp`,
`http://openai-mock:8090/v1`) — not `host.docker.internal`. Playwright on the
host types into the real composer and clicks Send. If MCP does not come back,
the run appends an anti-pattern to `test-results/antipatterns.jsonl` (see
`fixtures/antipatterns/catalog.yaml`) instead of treating that miss as “the
click never happened.”

```bash
mkdir -p test-results
docker compose -f docker/librechat-smoke/docker-compose.yml up --build -d
LIBRECHAT_BASE_URL=http://127.0.0.1:3080 \
  MCP_TOOLCALL_LOG=$PWD/test-results/mcp-toolcalls.jsonl \
  OPENAI_MOCK_LOG=$PWD/test-results/openai-mock.jsonl \
  pytest tests/real_chat_ui/test_librechat_docker.py -v
```

Skipped in default `pytest` (`LIBRECHAT_BASE_URL` unset). CI workflow:
`.github/workflows/librechat-docker-smoke.yml`. Docker logs (time / level /
message) land in `test-results/docker-logs/` on every smoke.

### Open WebUI in Docker + Playwright (input → Send → OpenAI + MCP wire)

`docker/openwebui-smoke/` starts published Open WebUI, `openwebui_mcp_mock.py`,
and the same OpenAI tool-call mock on **one Docker network**
(`mcp-toolcall-lab-openwebui`). Open WebUI reaches them by service DNS
(`http://mcp-mock:8000/mcp`, `http://openai-mock:8090/v1`). Playwright
types into `#chat-input` and clicks `#send-message-button`, then asserts
the product loop: `POST /v1/chat/completions` → `tool_calls[]` → MCP
`initialize` / `tools/list` / `tools/call` → `role: "tool"` follow-up
with the same `tool_call_id` → Yokohama in the UI. Product `chat.id` is
not treated as a lab `chat_*` pin.

This stack is CI/local Docker only. GitHub Pages stays static.

```bash
mkdir -p test-results
docker compose -f docker/openwebui-smoke/docker-compose.yml up --build -d
OPEN_WEBUI_BASE_URL=http://127.0.0.1:3000 \
  MCP_TOOLCALL_LOG=$PWD/test-results/mcp-toolcalls.jsonl \
  OPENAI_MOCK_LOG=$PWD/test-results/openai-mock.jsonl \
  pytest tests/real_chat_ui/test_openwebui_docker.py -v
```

Skipped in default `pytest` (`OPEN_WEBUI_BASE_URL` unset). Dedicated CI:
`.github/workflows/openwebui-docker-smoke.yml` (`workflow_dispatch`).
Details: [`docs/openwebui_mcp_notes.md`](docs/openwebui_mcp_notes.md).
After a smoke, container logs (time / level / message) are in
`test-results/docker-logs/` plus a merged `test-results/timeline.jsonl`:

```bash
python -m mcp_toolcall_lab.docker_logs capture \
  -f docker/openwebui-smoke/docker-compose.yml -o test-results/docker-logs
python -m mcp_toolcall_lab.docker_logs timeline \
  --dir test-results -o test-results/timeline.jsonl
```

### Serverless stub try (Actions + Pages)

GitHub Pages is static:
https://myon-bioinformatics.github.io/mcp-toolcall-lab/
Actions (`stub-pages`) starts Docker — stub UI + MCP mock + CPU-class
model on one network — then writes anti-pattern JSONL and publishes the
report. The published page is generation identity (Commit / optional
Version, `_site/build_meta.json`) plus induction to local `/wiki` and a
concise Last Actions snapshot — not a mock Wiki UI. Live Wikipedia title
form + heading select: `python -m mcp_toolcall_lab.stub_front serve --port 8765`
then open `/wiki`. See [`docs/stub_pages.md`](docs/stub_pages.md).
Local:

```bash
docker compose -f docker/stub-pages/docker-compose.yml up --build
python scripts/stub_pages_smoke.py
python -m mcp_toolcall_lab.stub_front pages --out _site --last-run test-results/last-run.json
```

The stub is `markdown.py` + the standard library. A real tiny GGUF behind
the same `cpu-llm` DNS is an opt-in overlay (`docker-compose.gguf.yml`,
`stub-pages.yml`'s `use_real_gguf` input) — off by default, since it
needs internet to huggingface.co that a plain push should not depend on.
Details: [`docs/stub_pages.md`](docs/stub_pages.md).

### Frontend catalog + one-liners (LibreChat / Open WebUI)

Playwright 用のセレクタ・認証・MCP の tool 名規則は
[`src/mcp_toolcall_lab/frontends.py`](src/mcp_toolcall_lab/frontends.py)
が単一ソースです（upstream の testid / id と provenance 付き）。
`chat_ui.py` がその定義を共有して type + Send します。

```bash
python -m mcp_toolcall_lab.frontends                 # 両クライアントの JSON
python -m mcp_toolcall_lab.frontends librechat
python -m mcp_toolcall_lab.chat_ui describe
python -m mcp_toolcall_lab.chat_ui describe openwebui
# UI が上がっているとき（pip install -e '.[browser-test]' && playwright install chromium）
python -m mcp_toolcall_lab.chat_ui send --client librechat --url http://127.0.0.1:3080
python -m mcp_toolcall_lab.chat_ui send --client openwebui --url http://127.0.0.1:3000
python -m mcp_toolcall_lab.chat_ui send --client librechat --chat-id chat_lab1
# after `pip install -e .` the same entry points:
mcp-frontends librechat
mcp-chat-ui send --client librechat --url http://127.0.0.1:3080
mcp-trace-probe --chat-id chat_lab1
python -m mcp_toolcall_lab.stub_front turn --heading Yokohama
python -m mcp_toolcall_lab.stub_front serve --port 8765
```

## Test

```bash
pip install -e '.[test]'
pytest -q
```

`pip install -e` already puts `src` on the import path, so `PYTHONPATH=src` is not required. Pull requests run the same commands on GitHub Actions with Python 3.11.

Wikipedia article screenshots (`python -m playwright screenshot` against the stub's `/wiki` form) are a dedicated workflow / manual lane: `.github/workflows/wikipedia-article-screenshot.yml`. They are not part of `pytest -q`. CI uses `fixtures/wikipedia/yokohama_extract.json`; live Wikipedia is `workflow_dispatch` `--live` or `MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1` locally. See [`docs/wikipedia_tool.md`](docs/wikipedia_tool.md).

## Talking to the mock with nothing but curl

Streamable HTTP is plain JSON-RPC over HTTP — no chat UI, browser, or even the `mcp`/`fastmcp`
Python SDKs are required to drive it. `scripts/mcp_curl_smoke.sh` performs the same handshake
Open WebUI does (`initialize` → `notifications/initialized` → `tools/list` → `tools/call`), using
only `curl` and `python3 -m json.tool` for pretty-printing:

```bash
python openwebui_mcp_mock.py &
scripts/mcp_curl_smoke.sh                      # defaults to http://127.0.0.1:8000/mcp
scripts/mcp_curl_smoke.sh http://host:port/mcp # or point it at another running instance
```

## Talking to the mock with FastMCP's own CLI

`fastmcp` ships a client CLI (`fastmcp list`, `fastmcp call`) alongside the server framework — since
`fastmcp==3.4.7` is already a pinned dependency here, this needs nothing beyond `pip install -e .`,
not even curl. It also handles the `initialize` / `notifications/initialized` / `Mcp-Session-Id`
handshake itself, so there's nothing to wire up by hand:

```bash
python openwebui_mcp_mock.py &
fastmcp list http://127.0.0.1:8000/mcp --json
fastmcp call http://127.0.0.1:8000/mcp find_municipalities query=Yokohama --json
scripts/mcp_fastmcp_cli_smoke.sh   # runs the above plus an empty-result and an unknown-tool case
```

One behavioral difference from curl or the `mcp` SDK: the CLI checks a tool name against its own
`tools/list` result before calling, so an unknown tool never reaches the server as an
`isError: true` `tools/call` — it fails client-side with a non-zero exit instead.
`tests/test_fastmcp_cli.py` covers both this and the success/empty-result cases; unlike
`tests/test_browser_fetch_protocol.py`, it needs no extra install and runs in CI along with
everything else in `pytest -q`.

## Talking to the mock with httpx — a plain Python client, no MCP SDK

`httpx==0.28.1` is already a pinned test dependency (`test_streamable_http_protocol.py` uses it
just to poll for server readiness). `tests/test_httpx_protocol.py` puts it to fuller use: a raw
Streamable HTTP client built on one reused `httpx.Client`, filling the gap between curl (reachable
from any shell, no Python) and the `mcp` SDK (the official, protocol-aware client) — the way a
lightweight Python service that doesn't want the full SDK as a dependency would actually talk to
this server.

Two things it demonstrates that curl and the SDK don't as directly:

- The same `httpx.Client` (and its `Mcp-Session-Id`) serves multiple calls in a row through one
  reused client/session (eligible for HTTP keep-alive) — curl spawns a brand-new process per
  call instead.
- Timing out is a plain `httpx.TimeoutException` on the raw request
  (`test_httpx_timeout_on_slow_tool`), not something that needs the `mcp` SDK's async
  cancellation machinery the way `test_timeout_raises_on_slow_tool` does in
  `test_streamable_http_protocol.py`.

## Tracing a call: chat-simulated vs. direct

A request can reach this mock two ways: **through a chat UI** (the model decides to call a tool,
the UI executes it) or **straight to MCP** (curl, a script, anything speaking Streamable HTTP
directly). Both are traceable through the same `MCP_TOOLCALL_LOG` JSONL file, correlated by
whatever the caller puts in the request's `_meta` field — MCP reserves that field for exactly this,
no protocol extension needed.

`mcp_toolcall_lab.chat_sim` mocks the "chat経由" path generically: the OpenAI-compatible
`tool_calls`/`tool`-role message shapes that Open WebUI, LibreChat, LobeChat, and most other chat
UIs share (rather than reimplementing any one product's own internal ids like Open WebUI's
`chat_id`/`function_id`), wired to a real MCP call tagged with `call_id`/`chat_id`/`source: "chat"`.
`send_direct` is the other path — no chat layer, tagged `source: "direct"`.

```python
import asyncio
from mcp_toolcall_lab.chat_sim import send_via_chat, send_direct

async def main():
    trace = await send_via_chat(
        "http://127.0.0.1:8000/mcp",
        user_text="Where is Yokohama?",
        tool_name="find_municipalities",
        arguments={"query": "Yokohama"},
    )
    print(trace.assistant_tool_call_message)  # {"role": "assistant", "tool_calls": [...]}
    print(trace.tool_result_message)          # {"role": "tool", "tool_call_id": ..., "content": ...}

    await send_direct("http://127.0.0.1:8000/mcp", tool_name="find_stations",
                       arguments={"municipality_code": "14109"}, trace_id="probe-1")

asyncio.run(main())
```

With `MCP_TOOLCALL_LOG` set, both calls above land in the same JSONL file with the same shape,
distinguished only by `meta.source` — see `tests/test_trace.py`. Each row also has
`event`, `duration_ms`, top-level `chat_id`, and `debug` (`chat_id_source` =
`meta` | `header` | `session` | `minted`). Caller `_meta` is not rewritten: if a
real UI omits `chat_id`, the server takes `X-Chat-Id` / `X-Conversation-Id` or
mints one per MCP session so the probe always has a pin.

JSONL I/O, `chat_*` / `call_*` mints, well-known chat headers, and the
HTTP/1.1 SSE close live in [`src/mcp_toolcall_lab/mock/common.py`](src/mcp_toolcall_lab/mock/common.py)
so the MCP server and `demos/openai_toolcall_mock.py` can share them. MCP
`_meta` resolution and OpenAI `tool_calls` decision stay in their own
files — overlapping helpers only, not a forced single mock.

### Stdlib stub front (heading → chat body)

LibreChat / Open WebUI stay the products under test. `stub_front` is a
zero-extra-dep reference UI: ATX markdown headings are the deterministic
model, the page reuses both products' composer locators, and `/c/{chat_id}`
puts that id on MCP `_meta` and `X-Chat-Id`. `GET /wiki` is a thin
title form plus a server-rendered heading select against a MediaWiki
plaintext extract (escaped; not on GitHub Pages). Roadmap for later slices
(Playwright-on-stub, `markdown` accuracy, CPU LLM in Docker — unnumbered):
[`docs/stub_front_roadmap.md`](docs/stub_front_roadmap.md).

```bash
python -m mcp_toolcall_lab.stub_front turn --heading "Yokohama"
python -m mcp_toolcall_lab.stub_front turn --heading "Find municipalities named Yokohama"
python -m mcp_toolcall_lab.stub_front serve --port 8765
# Wikipedia article → heading select (local server only; Pages has no backend)
# http://127.0.0.1:8765/wiki?title=Yokohama
# fixture screenshots (not part of pytest -q):
python scripts/wikipedia_article_screenshot.py --out test-results/wiki-screenshots
```

### Trace probe (the other ids)

`chat_id` is the lab-owned pin. Reasoning / response / UI hops mint more ids we do
**not** own: OpenAI `chatcmpl-*` / `call_*`, Responses `resp_` / `rs_` / `msg_` /
`fc_`, LibreChat `conversationId` in `/c/{id}`, Open WebUI `chat.id` / `/s/{share}`.
`mcp_toolcall_lab.trace_probe` harvests those from the MCP JSONL, the OpenAI mock
log, an observation row, and the page URL, then clusters hops that share any id
string (union-find). It is a probe, not a product database.

```bash
python -m mcp_toolcall_lab.trace_probe kinds
python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1 \
  --mcp-log test-results/mcp-toolcalls.jsonl \
  --openai-log test-results/openai-mock.jsonl \
  --url 'http://127.0.0.1:3080/c/66f012345678901234567890'
# after send, the observation already joins lab chat_id ↔ page_url ↔ completion/call ids
mcp-trace-probe --chat-id chat_lab1
```

`send --chat-id chat_*` only tags the observation. A product conversation id
(not the `chat_` prefix) resumes `/c/{id}` after login.

`tests/test_curl_protocol.py` gives the same raw-HTTP handshake pytest coverage (success, empty
result, unknown tool, missing argument), alongside `tests/test_streamable_http_protocol.py`'s
`mcp`-SDK-based client flow.

`chat_sim.py` mocks the generic OpenAI-compatible tool-calling wire shape, not any one chat UI's
own database. See [`docs/openwebui_schema_notes.md`](docs/openwebui_schema_notes.md) for Open
WebUI's actual `chat`/`file`/`function`/`tool` table schemas (verified against its source), kept
as a reference for a dedicated Open WebUI-specific mock later. See
[`docs/librechat_mcp_notes.md`](docs/librechat_mcp_notes.md) for the same kind of notes on
LibreChat, this lab's second chat client.

### Proving the loose HTTP coupling from a real browser

This mock has **no chat screen** — `GET /` is a plain 404 and `/mcp` only speaks JSON-RPC, so
there's no UI to click through or screenshot (Open WebUI's actual chat interface is a separate
application entirely). What's still worth proving is that the API is reachable from a real
browser's own `fetch()`, not just curl or a Python client:

```bash
pip install -e '.[test,browser-test]'
playwright install chromium
pytest -q tests/test_browser_fetch_protocol.py
```

`tests/test_browser_fetch_protocol.py` navigates a headless Chromium to the server's own origin
(same-origin, so no CORS is needed — the server sends no `Access-Control-Allow-Origin` header and
405s on OPTIONS preflight, so a *cross*-origin browser fetch would be blocked) and runs the same
`initialize`/`tools/list`/`tools/call` handshake as `test_curl_protocol.py`, purely through
`page.evaluate(() => fetch(...))`. Not part of `test` extras or CI — it `pytest.importorskip`s
when `playwright` isn't installed, same as every other optional path in this repo.

## Empty vs error

- **Empty** is a successful `tools/call` whose result is `[]` (unknown municipality, blank query, or a Japanese name that is not in this tiny English mock). Open WebUI forwards `content`, so the model sees an empty list, not a protocol error.
- **Error** is an unknown tool name, a missing/invalid argument, or a client timeout. Those calls set `isError` or raise at the client. They are not empty results.

Set `MCP_TOOLCALL_LOG=toolcalls.jsonl` before launch to record every `tools/call` as JSON Lines, including empty results and errors. The same logger is used by `python -m mcp_toolcall_lab` and by the standalone file. `arguments` in the log are the values received on the wire, before pydantic coercion.

## Prompt / model experiment fixtures (offline)

Inputs are official OpenAI Chat Completions `tools` / `tool_calls` plus recorded
MCP Streamable HTTP hops: JSON-RPC 2.0 request/response on `POST /mcp`
(`initialize` → `notifications/initialized` → `tools/list`, then
`tools/call` when the model selected a tool). Replay does not change that
wire, call a model, or reach MLIT. Comparison JSONL is not a wire log.

Use `system_prompts/strict_tool_selection.md`. The model may only choose among
the advertised specs. A fictional tool name is a failure even if the intended
action sounds correct. **raw schema valid** (arguments match advertised
`parameters` before coercion) is separate from **server accepted** (MCP
`tools/call` outcome is not `error`).

| id | Model / settings | selected tool | raw schema valid | server accepted | outcome | notes |
| --- | --- | --- | --- | --- | --- | --- |
| `available_tool_success` | lab-model / temperature 0, reasoning.effort=low, stop=[] | `find_municipalities` | true | true | success | advertised tool only; UI `chat_id` / message id / `call_*` bind the `tools/call` return to `role:tool` |
| `fictional_tool_reject` | lab-model / temperature 0, reasoning.effort=low, stop=[] | _(none)_ | — | — | — | refuses `query_reinfoldib`; no sent `tools/call`; server `isError` envelope recorded separately |

```bash
python -m mcp_toolcall_lab.prompt_experiment replay --out test-results/prompt-experiments.jsonl
```

The JSONL is comparison/audit fields only (verdict, selected/fictional tools,
finish_reason, schema flags). It is not a substitute for the HTTP/JSON-RPC
records. Fixtures: `fixtures/prompt_experiments/`.

## Next increments

1. Add a versioned mock catalogue modeled on public REINFOLIB documentation, without API keys.
2. Compare schema strictness (raw-valid vs server-accepted) and system prompts in a recorded experiment matrix.
