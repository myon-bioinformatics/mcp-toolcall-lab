# E2E foundation (issue #14)

This documents the shared E2E foundation added for issue #14, narrowed per the
coordination note on that issue: **#13 owns LibreChat's real Docker/Playwright
E2E work**; this repo's #14 pieces extend the same architecture to Gradio,
Streamlit, and Open WebUI plus the shared infrastructure, and deliberately do
not duplicate LibreChat's Docker/Playwright/CI implementation.

## Client roles

Kept distinct in code and docs, per issue #14:

| Role | Clients | Notes |
| --- | --- | --- |
| Clients under test | Open WebUI, LibreChat | Real external chat products. LibreChat's Docker/Playwright coverage lives in #13; Open WebUI's lives here. |
| Reference / diagnostic clients | Gradio, Streamlit | Thin adapters (`apps/`) over the shared `mcp_toolcall_lab.reference_client` layer — a human picks the tool/arguments, no model in the loop. A successful call through either helps tell a product-specific failure apart from an MCP/server-wide one. |
| Protocol-level clients | MCP SDK, httpx, curl, FastMCP CLI, browser `fetch()` | See the README sections on each; `tests/test_browser_fetch_protocol.py` is the existing browser-fetch coverage, distinct from the `chat-e2e` lane below. |

Open WebUI and LibreChat are **not** wrappers around `reference_client.py` —
they are real products, driven end-to-end through their own UI.

## Shared reference-client layer

`src/mcp_toolcall_lab/reference_client.py` is the one place that talks to MCP
on behalf of Gradio and Streamlit: tool discovery (`discover_tools`) and
tool calling with success/empty/error classification
(`call_tool_for_prompt`). `apps/gradio_app.py` and `apps/streamlit_app.py` are
thin UI adapters over it — neither should grow its own MCP client code.

```
shared chat / MCP client (reference_client.py)
  |- tool discovery / call
  |- success / empty / error classification
  |- case_id / client / source="reference" tagging
       |
       +-- Gradio adapter (apps/gradio_app.py)
       +-- Streamlit adapter (apps/streamlit_app.py)
```

## Test lanes

### 1. Normal PR lane

Runs as part of the existing `pip install -e ".[test]"` / `pytest -q` — no
Docker, no browser:

- `tests/test_reference_client.py` — browserless coverage of
  `reference_client.py`'s discovery/calling/classification against this
  repo's own mock server (`running_mcp_server`, the same subprocess fixture
  every other integration test here already uses).
- `tests/test_reference_apps.py` — Gradio `Blocks` build check and
  Streamlit's own headless `AppTest` harness. Both `pytest.importorskip` when
  the optional `gradio`/`streamlit` extras aren't installed
  (`pip install -e ".[reference-ui]"`), matching the existing
  `browser-test`/Playwright pattern.

Neither test boots a real chat product (Open WebUI/LibreChat), matching the
issue's "no requirement to boot every real chat product on every PR."

### 2. `chat-e2e` lane (Open WebUI here; LibreChat in #13)

`tests/e2e/test_openwebui_chat_e2e.py` drives Open WebUI's real chat UI with
Playwright: UI starts, chat page opens, a prompt is entered, Send is pressed,
an assistant response is awaited. It self-skips (`pytest.importorskip` /
`skipif`) when Playwright isn't installed or `OPENWEBUI_URL` isn't reachable,
so it is inert in the normal PR lane — run it against
`docker compose up openwebui mcp-mock` explicitly:

```bash
docker compose up --build mcp-mock openwebui
pip install -e ".[test,browser-test]" && playwright install chromium
OPENWEBUI_URL=http://127.0.0.1:3000 pytest -q tests/e2e/test_openwebui_chat_e2e.py
```

**Known gap:** registering `mcp-mock` as an Open WebUI tool server is a
stored-in-DB admin action with no environment variable today, so this test
covers UI input + Send + assistant response only (the issue's minimum bar),
not `chat UI -> tool selection -> mock MCP -> tool result -> assistant
response` yet. That extension is follow-up work once tool-server
registration is scripted (or Open WebUI adds a way to seed it).

LibreChat's equivalent `chat-e2e` coverage is owned by #13 and intentionally
not duplicated here.

### 3. Manual / limited browser smoke lane (Gradio, Streamlit)

`tests/e2e/test_gradio_smoke.py` and `tests/e2e/test_streamlit_smoke.py`:
open app, enter prompt, submit, render result. Same self-skip behavior as the
`chat-e2e` test above. Meant for manual/`workflow_dispatch` use, not every
PR:

```bash
docker compose up --build mcp-mock gradio streamlit
GRADIO_URL=http://127.0.0.1:7860 pytest -q tests/e2e/test_gradio_smoke.py
STREAMLIT_URL=http://127.0.0.1:8501 pytest -q tests/e2e/test_streamlit_smoke.py
```

Selectors in all three `tests/e2e/` specs are best-effort against each app's
own markup and have not been verified against a live instance in this
environment (no browser/Docker execution was available while writing them) —
expect to adjust them once actually run.

## Docker / network model

`docker-compose.yml` defines `mcp-mock`, `gradio`, `streamlit`, and
`openwebui` on the project's default Compose network. Every client resolves
the mock at the Docker service DNS name, never a container IP or
`localhost`:

```
http://mcp-mock:8000/mcp
```

`docker/*.Dockerfile` build `mcp-mock`/`gradio`/`streamlit` from this repo's
own source; `openwebui` uses the upstream `ghcr.io/open-webui/open-webui`
image directly.

**LibreChat is intentionally not a service in this file** — #13 owns its
Docker/Playwright/CI implementation. To exercise LibreChat against the same
`mcp-mock`, run #13's compose file alongside this one:

```bash
docker compose -f docker-compose.yml -f <path-to-#13's-compose-file> up
```

For the two files to combine cleanly, both must resolve the mock at the
literal service name `mcp-mock:8000/mcp` on the default network rather than
defining a second mock service — that is the shared convention this file
follows and the one #13's file should follow too.

## E2E observability foundation

`reference_client.py`'s `ToolCallCase` dataclass uses the exact field names
issue #14 asks a later case record to capture: `case_id`, `client`, `prompt`,
`tool_called`, `tool_name`, `arguments`, `mcp_outcome`,
`assistant_received_result`, `classification`. Each reference-client call is
also tagged in the MCP request's `_meta` (`case_id`, `client`,
`source: "reference"`), so it lands in `MCP_TOOLCALL_LOG` (see
`record.py`/README's "Tracing a call" section) alongside `chat_sim.py`'s
existing `"chat"`/`"direct"` calls — one JSONL schema, distinguished only by
`meta.source`/`meta.client`. A later correlation pass can join a
`ToolCallCase` against its `MCP_TOOLCALL_LOG` row by `case_id` without
inventing a second scheme. The full anti-pattern catalogue and reporting
layer remain deferred, per the issue's own scope note.
