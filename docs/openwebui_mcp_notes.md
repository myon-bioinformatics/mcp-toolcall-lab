# Open WebUI + this mock (Streamable HTTP)

This lab's MCP server stays the **same** Streamable HTTP endpoint LibreChat
already uses (`/mcp`). Open WebUI is the first-class native Streamable HTTP
client: same mock, different wiring surface (Admin Integrations / 
`TOOL_SERVER_CONNECTIONS`).

Verified against Open WebUI's public docs
([MCP feature](https://docs.openwebui.com/features/extensibility/mcp/))
and `open-webui/open-webui` (`utils/mcp/client.py` uses the MCP SDK
`streamablehttp_client` + `session.initialize()` / `list_tools()` /
`call_tool()`). Cross-checked 2026-09-19.

GitHub Pages does **not** host this stack. Pages stays a static report.
The live Open WebUI + mock network exists only while Docker / Actions runs.

## `openwebui_mcp_mock.py`

`python -m mcp_toolcall_lab.export` writes two standalone files,
[`openwebui_mcp_mock.py`](../openwebui_mcp_mock.py) and
[`librechat_mcp_mock.py`](../librechat_mcp_mock.py), both generated from the
exact same `src/mcp_toolcall_lab/` source. Run the Open WebUI-named copy
when this product is the client under test:

```bash
python openwebui_mcp_mock.py
```

Do not add a second MCP protocol, a custom envelope, or a decision-trace
schema. The OpenAI-compatible wire (`POST /v1/chat/completions`,
`tool_calls[]`, `role: "tool"` + `tool_call_id`) and MCP JSON-RPC
(`initialize`, `notifications/initialized`, `tools/list`, `tools/call`)
are the source of truth.

## Docker + Playwright smoke (chat input → Send → MCP)

`docker/openwebui-smoke/` runs **real Open WebUI** (published image) on the
same compose network (`mcp-toolcall-lab-openwebui`) as:

1. `mcp-mock` — this repo's Streamable HTTP MCP server (`openwebui_mcp_mock.py`)
2. `openai-mock` — `demos/openai_toolcall_mock.py`, a stdlib OpenAI-compatible
   server that emits `tool_calls` for Yokohama / municipalities (Open WebUI
   prefixes MCP tools as `{info.id}_{tool}` on the OpenAI wire, e.g.
   `lab_find_municipalities`; the MCP `tools/call` name stays
   `find_municipalities`)

Open WebUI talks to them by service DNS (`http://mcp-mock:8000/mcp`,
`http://openai-mock:8090/v1`). Playwright on the host types into
`#chat-input` and clicks `#send-message-button`. Selectors live in
[`src/mcp_toolcall_lab/frontends.py`](../src/mcp_toolcall_lab/frontends.py).

`TOOL_SERVER_CONNECTIONS` (type `mcp`, not OpenAPI) plus
`DEFAULT_MODEL_METADATA.toolIds=["server:mcp:lab"]` pre-selects the mock
so the turn can attach tools without a full admin-UI walkthrough.
`ENABLE_FORWARD_USER_INFO_HEADERS=True` and per-connection
`{{CHAT_ID}}` / `{{MESSAGE_ID}}` headers forward Open WebUI's own
`chat.id` / message id. Those are **product** ids. Lab `chat_*` pins
from `chat_sim` / mint-on-missing-header are a different kind; the smoke
asserts they are not treated as the same value.

The Playwright job asserts the product loop, not a lab stand-in:

1. UI input → Send
2. OpenAI-compatible `POST /v1/chat/completions` with `tools`
3. assistant `tool_calls[]` (`call_*`)
4. MCP Streamable HTTP `initialize` (and `notifications/initialized` when sent)
5. `tools/list` → `tools/call`
6. follow-up completion with `role: "tool"` and the same `tool_call_id`
7. final assistant text containing Yokohama in `#response-content-container`
8. UI message id: `X-OpenWebUI-Message-Id` on MCP `tools/call` equals a
   message id from Open WebUI's own `GET /api/v1/chats/{id}` history
   (not a lab `chat_*` / `call_*`, not an unsubstituted `{{MESSAGE_ID}}`).
   If the product version does not send that header, or the chats API
   does not yield the same id, the smoke fails — it does not PASS.
   Two Playwright Sends share one OpenAI JSONL; the helper selects the last
   complete `tool_calls[]` → `role:"tool"` pair (not first-wins / last-row-wins),
   and requires the follow-up `tool_call_id` (not just inbound ids).

Open WebUI may also POST `/v1/chat/completions` for background jobs whose
user text starts with `### Task:` (titles, tags, follow-ups). Those are
not the chat turn under test. The smoke helper skips them, and
`docker/openwebui-smoke/env.smoke` turns title/tags/follow-up generation
off so they are less likely to run. Product `chat.id` is taken from the
`/c/{id}` URL when present, otherwise from the OpenAI/MCP log headers —
not from a lab `chat_*` pin.

JSONL artifacts are projections of that wire (OpenAI mock request/response
IDs, MCP method rows). They do not replace the wire.

Smoke workflows always capture ``docker compose logs --timestamps`` into
`test-results/docker-logs/` (raw `compose.log` plus JSONL with `at` /
`level` / `message` / `service`) and merge them with MCP + OpenAI JSONL
into `test-results/timeline.jsonl`. That is how to read OWUI's own log
text next to `initialize` / `tools/list` / `tools/call`. The MCP mock
also prints the same events on stderr so they show up in the compose
capture. Pages still does not host this stack.

```bash
python -m mcp_toolcall_lab.docker_logs capture \
  -f docker/openwebui-smoke/docker-compose.yml \
  -o test-results/docker-logs
python -m mcp_toolcall_lab.docker_logs timeline \
  --dir test-results -o test-results/timeline.jsonl
```

```bash
mkdir -p test-results
docker compose -f docker/openwebui-smoke/docker-compose.yml up --build -d
# wait for http://127.0.0.1:3000/health
OPEN_WEBUI_BASE_URL=http://127.0.0.1:3000 \
  MCP_TOOLCALL_LOG=$PWD/test-results/mcp-toolcalls.jsonl \
  OPENAI_MOCK_LOG=$PWD/test-results/openai-mock.jsonl \
  pytest tests/real_chat_ui/test_openwebui_docker.py -v
```

Skipped in default `pytest` (`OPEN_WEBUI_BASE_URL` unset). CI workflow:
`.github/workflows/openwebui-docker-smoke.yml` (`workflow_dispatch` and
path-filtered `pull_request`). Default `test.yml` stays `pytest -q`.

## What this lane does / does not do

**Does:** run the real Open WebUI image against the existing mock + OpenAI
tool-call mock, drive the composer with Playwright, harvest product
`chat.id` / OpenAI `chatcmpl-*` / `call_*` / UI message id / MCP
session and request ids, record the running `open-webui` image digest, and
upload JSONL plus screenshots.

**Does not:** vendor Open WebUI's source, reproduce its SQLite schema,
call MLIT or any live model API, inject hidden chain-of-thought or
prompt noise, change GitHub Pages into a live backend, or pull this
Docker/Playwright path into ordinary `pytest -q`.
