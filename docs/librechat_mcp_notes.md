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

## `librechat_mcp_mock.py`

`python -m mcp_toolcall_lab.export` now writes two standalone files,
[`openwebui_mcp_mock.py`](../openwebui_mcp_mock.py) and
[`librechat_mcp_mock.py`](../librechat_mcp_mock.py), both generated from the exact same
`src/mcp_toolcall_lab/` source. They differ only in their own header docstring (which file
to copy, what to run) -- there is still one mock implementation, not two; regenerating one
regenerates both, and `tests/test_schema.py` asserts everything past that header is
byte-identical between them. Run whichever one matches the product you're pointing at, for
the same reason a README is easier to follow when the file it tells you to run is named
after the thing you're actually using:

```bash
python librechat_mcp_mock.py
```

## Minimal `librechat.yaml` fragment

See [`examples/librechat.mcp.example.yaml`](../examples/librechat.mcp.example.yaml).
Copy the `mcpServers:` block into your LibreChat config (merge with your existing file;
do not replace unrelated keys).

Important fields for this mock:

- `type: streamable-http` — matches FastMCP's `/mcp` endpoint (do **not** use OpenAPI).
- `url` — same host rules as Open WebUI (`127.0.0.1`, `host.docker.internal`, or service DNS).
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

## What this PR does / does not do

**Does:** document LibreChat as a second client, ship a copy-paste YAML example, add a
strict prompt twin, a same-purpose `librechat_mcp_mock.py` standalone file (generated, not
forked), and lock both the example shape and the standalone file's byte-for-byte parity
with a pytest guard.

**Does not:** vendor LibreChat, run LibreChat in CI, or claim a full product mock of
LibreChat's DB / Agents marketplace. Protocol truth remains curl / httpx / `mcp` SDK /
FastMCP CLI against the shared mock.
