# E2E foundation (issue #14): Docker Compose + Playwright across clients

This is the foundation issue #14 asked for: the execution paths and
boundaries for three test lanes, kept distinct, all pointed at the **same**
mock MCP implementation. It does not attempt the full anti-pattern
catalogue described in that issue -- see "Deferred / follow-up work" below.

## Client roles

Preserved everywhere in this repo (code, docs, this file): a client's role
determines what a pass/fail from it actually tells you.

| Bucket | Clients | What a result from it means |
| --- | --- | --- |
| Clients under test | Open WebUI, LibreChat | The actual product behavior this lab exists to exercise. |
| Reference / diagnostic clients | Gradio, Streamlit | Thin adapters with no product-specific behavior of their own (see below) -- a pass here while a client-under-test fails narrows the failure to that product, not to MCP/the mock server. |
| Protocol-level clients | MCP SDK, httpx, curl, FastMCP CLI, existing browser `fetch()` | Ground truth for "is the server itself doing the right thing," independent of any UI. |

## Shared chat/MCP client layer

Gradio and Streamlit do **not** each implement their own MCP client.
Both import `src/mcp_toolcall_lab/reference_client.py`:

```
mcp_toolcall_lab.reference_client
  |- discover_tools()   -- tools/list, normalized to ToolSpec
  |- call_tool()        -- tools/call, tagged with case_id/client/source="reference"
  |- ToolCallResult      -- normalized success/empty/error outcome
       |
       +-- apps/gradio_app.py     (thin adapter)
       +-- apps/streamlit_app.py  (thin adapter)
```

Open WebUI and LibreChat are **not** wrapped by this layer -- they remain
real, independent products under test, per the issue.

This is a different module from `src/mcp_toolcall_lab/chat_sim.py`, on
purpose: `chat_sim` mocks the OpenAI-compatible `tool_calls` wire shape a
real chat UI's *model* would produce (`source: "chat"` vs `"direct"` in
MCP_TOOLCALL_LOG); `reference_client` is what a UI with no LLM in the loop
(Gradio/Streamlit here) uses to call a tool the user picked explicitly
(`source: "reference"`). Same log, same success/empty/error semantics,
different `meta.source`.

## Docker / network model

`docker-compose.yml` puts every service on the default Compose network, so
service-name DNS resolves container-to-container without depending on
container IPs or `localhost` (which inside a container means that
container's own namespace):

```
mcp-mock   (http://mcp-mock:8000/mcp)
mock-llm   (http://mock-llm:8081/v1)  -- deterministic OpenAI-style replies

  mcp-mock  <- openwebui, librechat, gradio, streamlit
  mock-llm  <- openwebui, librechat
```

`mock-llm` (`docker/mock_llm.py`) exists only so Open WebUI/LibreChat have
*something* to answer with -- issue #14's chat-e2e minimum bar includes
"assistant response can be awaited," which needs a model backend of some
kind. It is stdlib-only, deterministic, and returns one fixed reply; it does
not speak MCP and is not part of the server under test.

**Not executed end-to-end in the environment that produced this PR** (no
Docker registry/daemon access there). The compose file and LibreChat config
(`docker/librechat.compose.yaml`) are written from each product's public
docs/images, the same way `docs/librechat_mcp_notes.md` already flags its
own config as unverified against a live container. Validate before relying
on it:

```bash
docker compose up --build
```

Known gap: registering `mcp-mock` as an Open WebUI "MCP (Streamable HTTP)"
tool server is a stored-in-DB admin action today (see the README's "Run
locally" section) -- there is no environment variable for it yet. The
chat-e2e Playwright test for Open WebUI therefore covers UI input + Send
only (the issue's minimum bar), not tool selection.

## Test lanes

### 1. Normal PR lane (already wired into `.github/workflows/test.yml`)

```bash
pip install -e ".[test]"
pytest -q
```

- `tests/test_reference_client.py` -- browserless coverage of
  `reference_client.py` against a local mock-server subprocess (same
  `running_mcp_server` helper the other protocol tests use). No Docker.
- `tests/test_reference_apps.py` -- Gradio import/Blocks-build check and a
  Streamlit `AppTest` headless run. No browser.
- Everything under `tests/e2e/` collects but self-skips: Playwright ships in
  the `browser-test` extra, not `test`, so these are inert here -- exactly
  like `tests/test_browser_fetch_protocol.py` already is.

### 2. `chat-e2e` lane (Open WebUI, LibreChat)

Not wired into CI by this PR -- **Claude's GitHub App permissions do not
allow writing to `.github/workflows/`**, so a maintainer needs to add the
workflow. A ready-to-copy starting point is at
[`docs/ci/chat-e2e.workflow.yml.example`](ci/chat-e2e.workflow.yml.example);
copy it to `.github/workflows/chat-e2e.yml` to activate it.

To run locally:

```bash
docker compose up -d --build mcp-mock mock-llm openwebui librechat librechat-mongo
pip install -e ".[test,browser-test]"
playwright install chromium
pytest -q -m chat_e2e tests/e2e
docker compose down -v
```

Covers issue #14's minimum bar (UI starts, chat page opens, prompt entered,
Send pressed, assistant response awaited) for both products. Tool-selection
coverage (`chat UI -> tool selection -> mock MCP -> tool result -> assistant
response`) is follow-up work, gated on the Open WebUI tool-registration gap
above and on picking real selectors against a live instance (see the
"Known limitations" note in each test module's docstring).

### 3. browser-smoke lane (Gradio, Streamlit)

Same activation caveat and example workflow:
[`docs/ci/browser-smoke.workflow.yml.example`](ci/browser-smoke.workflow.yml.example).

```bash
docker compose up -d --build mcp-mock gradio streamlit
pip install -e ".[test,browser-test]"
playwright install chromium
pytest -q -m browser_smoke tests/e2e
docker compose down -v
```

Unlike the chat-e2e selectors, `tests/e2e/test_gradio_smoke.py` and
`test_streamlit_smoke.py` target this repo's own `apps/*.py` labels, so
they are not a guess against unfamiliar markup.

## Observability foundation

`MCP_TOOLCALL_LOG` (see `record.py`) already logs every `tools/call` with
whatever the caller put in the request's MCP `_meta`. `reference_client.py`
tags `case_id`/`client`/`source: "reference"`; `chat_sim.py` tags
`call_id`/`chat_id`/`source: "chat"` or `source: "direct"`. A future case
record (per issue #14) can join browser activity to this log without a
rewrite of either module:

| Future case-record field | Where it already lives |
| --- | --- |
| `case_id` | `ToolCallResult.case_id` / `_meta.case_id` |
| `client` | `ToolCallResult.client` / `_meta.client` |
| `prompt` | `ToolCallResult.prompt` (Gradio/Streamlit); a chat-e2e test's own typed prompt |
| `tool_called`, `tool_name`, `arguments` | `ToolCallResult` fields; also present on every MCP_TOOLCALL_LOG row |
| `mcp_outcome` | `ToolCallResult.outcome` (`success`/`empty`/`error`, same semantics as `record.py`) |
| `assistant_received_result` | `ToolCallResult.assistant_received_result` |
| `classification` | not built here -- the anti-pattern catalogue below decides this later |

This PR does not build the reporting/correlation layer itself (out of
scope per the issue), just avoids shapes that would make it hard later.

## Deferred / follow-up work

Explicitly out of scope for this issue, per its own "suggested
implementation sequence":

- The full anti-pattern catalogue (UI submit failed, model did not call a
  tool, fictional/unknown tool, argument schema mismatch, transport
  failure, server `isError`, empty result, timeout, MCP success but no
  propagation to chat, result propagated but final answer incorrect).
- Wiring `chat-e2e` / browser-smoke into actual CI (permissions gap above).
- Open WebUI/LibreChat tool-selection coverage (needs the registration gap
  above resolved, plus real selectors validated against a live instance).
- Richer Playwright artifacts (traces, screenshots on failure) beyond what
  the example workflows sketch.
