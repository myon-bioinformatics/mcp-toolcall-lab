# LibreChat + this mock (Streamable HTTP)

This lab's MCP server stays the **same** Streamable HTTP endpoint Open WebUI already
uses (`/mcp`). LibreChat is the second first-class chat client: same mock, different
wiring surface (`librechat.yaml` / Agents / chat MCP picker).

Verified against LibreChat's public docs
([MCP feature](https://www.librechat.ai/docs/features/mcp),
[mcpServers object](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers))
and connection notes in `danny-avila/LibreChat` (Streamable HTTP transport).
Fetched / cross-checked 2026-09-18.

## Why LibreChat next

| | Open WebUI (already documented) | LibreChat (this PR) |
| --- | --- | --- |
| Role in this lab | First client; native Streamable HTTP | Second client; multi-transport MCP + Agents |
| Config surface | UI "MCP (Streamable HTTP)" connection | `librechat.yaml` `mcpServers` (+ UI panel) |
| Transports | Streamable HTTP native; others often via proxy | **stdio / sse / streamable-http / websocket** |
| Production note | — | Docs recommend **Streamable HTTP** for multi-user |

Keep one mock process; point both UIs at it. That is the experiment.

## Minimal `librechat.yaml` fragment

See [`examples/librechat.mcp.example.yaml`](../examples/librechat.mcp.example.yaml).
Copy the `mcpServers:` block into your LibreChat config (merge with your existing file;
do not replace unrelated keys).

Important fields for this mock:

- `type: streamable-http` — matches FastMCP's `/mcp` endpoint (do **not** use OpenAPI).
- `url` — same host rules as Open WebUI (`127.0.0.1`, `host.docker.internal`, or compose service DNS such as `http://mcp-mock:8000/mcp`).
- `requiresOAuth: false` — this mock has no OAuth; without it LibreChat may probe OAuth first.
- `startup: true` — optional; shared server initialized at startup for all users (LibreChat's
  meaning of "startup", not "run a subprocess").
- No `command` / `args` — those are for **stdio** servers only.

After editing YAML, restart LibreChat (file-based MCP entries). Servers added only in the
UI panel can apply without restart — see LibreChat docs.

## Chat picker vs Agents

LibreChat exposes MCP two ways (both can use this mock):

1. **Chat MCP picker** — pick the server under a normal endpoint; all of that server's tools
   become available for the turn (good for "did the model only call advertised tools?").
2. **Agents** — attach MCP servers to an agent; useful for longer workflows / skills later.

For this lab's first LibreChat pass, prefer the **chat picker** path so the comparison with
Open WebUI stays "same tools/list → tools/call" shaped. Agents are a follow-up axis.

## Prompt

Use [`system_prompts/strict_tool_selection_librechat.md`](../system_prompts/strict_tool_selection_librechat.md)
the same way as the Open WebUI strict prompt: after LibreChat has completed `initialize` /
`tools/list`, bind the model to only those advertised specs.

## Same mock URLs as Open WebUI

| Where LibreChat runs | Server URL |
| --- | --- |
| Same host as the mock | `http://127.0.0.1:8000/mcp` |
| Docker, mock on the host | `http://host.docker.internal:8000/mcp` |
| Another VM / compose service | `http://<mock-hostname>:8000/mcp` |

Mock must listen with `MCP_HOST=0.0.0.0` when the client is not in the same network namespace.
Auth: none unless you add a token yourself (then mirror it in `headers:`).

## Docker + Playwright smoke (chat input → Send → MCP)

`docker/librechat-smoke/` runs **real LibreChat** (published image) on the same
compose network (`mcp-toolcall-lab`) as:

1. `mcp-mock` — this repo's Streamable HTTP MCP server (`openwebui_mcp_mock.py`)
2. `openai-mock` — `demos/openai_toolcall_mock.py`, a stdlib OpenAI-compatible
   server that emits `tool_calls` for Yokohama / municipalities (LibreChat
   prefixes MCP tools as `find_municipalities_mcp_<server>`)

LibreChat talks to them by service DNS (`http://mcp-mock:8000/mcp`,
`http://openai-mock:8090/v1`). That is more reproducible than host-network
mocks plus `host.docker.internal`. Playwright on the host still types into
`[data-testid=text-input]` and clicks `[data-testid=send-button]`. That UI
contract is a hard assertion. Selectors, OWUI twins (`#chat-input` /
`#send-message-button`), and MCP tool-key rules live in
[`src/mcp_toolcall_lab/frontends.py`](../src/mcp_toolcall_lab/frontends.py)
and are driven by `python -m mcp_toolcall_lab.chat_ui`.

Whether MCP actually ran is classified afterwards
(`src/mcp_toolcall_lab/antipatterns.py` + `fixtures/antipatterns/catalog.yaml`):

| Verdict | Meaning |
| --- | --- |
| PASS | Send reached MCP and the chat showed Yokohama |
| ANTIPATTERN / `MCP_PICKER_OFF` | `/v1/chat/completions` had no `tools` |
| ANTIPATTERN / `MCP_NOT_CALLED` | Send worked; `MCP_TOOLCALL_LOG` is empty |
| ANTIPATTERN / `UI_NO_RESULT` | MCP succeeded; the transcript did not |

Hits are appended to `test-results/antipatterns.jsonl` (CI artifact). The catalog
is the dictionary; the JSONL is the accumulating ledger. A miss does **not** fail
the MCP-classification test — it is the record. The send-click test still fails
if the composer itself is broken.

```bash
mkdir -p test-results
docker compose -f docker/librechat-smoke/docker-compose.yml up --build -d
# wait for http://127.0.0.1:3080
LIBRECHAT_BASE_URL=http://127.0.0.1:3080 \
  MCP_TOOLCALL_LOG=$PWD/test-results/mcp-toolcalls.jsonl \
  OPENAI_MOCK_LOG=$PWD/test-results/openai-mock.jsonl \
  pytest tests/real_chat_ui/test_librechat_docker.py -v
```

GitHub Actions: `.github/workflows/librechat-docker-smoke.yml` (`workflow_dispatch`
and `pull_request`; default `pytest -q` stays lean and skips this module).

`librechat.yaml` for this smoke uses service names. The copy-paste fragment in
`examples/librechat.mcp.example.yaml` still documents `127.0.0.1` /
`host.docker.internal` for a mock that is *not* in the same compose file.

## What this lab does / does not do

**Does:** document LibreChat as a second client, ship a copy-paste YAML example, add a
strict prompt twin, run an optional real-LibreChat Docker + Playwright smoke that
finishes chat input/Send and records MCP misses as anti-patterns.

**Does not:** vendor LibreChat's source, claim a full product mock of its DB /
Agents marketplace, or gate every default `pytest` on a container pull. Protocol
truth remains curl / httpx / `mcp` SDK / FastMCP CLI against the shared mock.
