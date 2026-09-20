# Wikipedia-only Streamable HTTP MCP (Deno Deploy)

Keyless MCP endpoint that advertises **only** `fetch_wikipedia_article` and
`fetch_wikipedia_section`. Pages `#wiki` is **not** wired to this server in
this change; that connection (and any OpenAI `tools` / `tool_calls` → MCP →
`role: tool` browser loop) is a follow-up.

No MLIT, no API keys, no custom JSON event rows. The wire is official MCP
Streamable HTTP: JSON-RPC 2.0 on `POST /mcp`, SSE `event: message` for
responses, empty **202** for `notifications/initialized`, `Mcp-Session-Id`,
and CORS so a later github.io client can `fetch()` it.

Tool `inputSchema` is generated from the Python FastMCP catalog
(`python -m mcp_toolcall_lab.wikipedia_mcp_catalog` →
`catalog.generated.json`). Do not edit that JSON by hand.

## Run locally

Deno 2.x (no npm install):

```bash
cd deploy/wikipedia-mcp
deno task start
# POST http://127.0.0.1:8000/mcp
```

Optional: `MCP_HOST`, `MCP_PORT` / `PORT`, and
`MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE=/abs/path/to/fixtures/wikipedia/yokohama_extract.json`
to serve the vendored MediaWiki extract instead of calling Wikipedia.

Handshake (same product order as Open WebUI / PR #23 fixtures):

```bash
# initialize
curl -sS -D - -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}'

# copy Mcp-Session-Id from the response headers, then:
curl -sS -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Mcp-Session-Id: SESSION' \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl -sS -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Mcp-Session-Id: SESSION' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

`GET /health` is a deploy probe (`{"ok": true, ...}`), not an MCP method.

## Deno Deploy

1. Create a project pointed at this repository.
2. Set the entrypoint to `deploy/wikipedia-mcp/main.ts` (root directory = repo root, or set the project root to `deploy/wikipedia-mcp` and entrypoint `main.ts`).
3. Do not set secrets. Wikimedia needs a `User-Agent`; the server sends a lab UA on the server-side fetch.
4. After deploy, the public URL is `https://<project>.deno.dev/mcp`.

Permissions used at runtime: `--allow-net` (listen + Wikipedia), `--allow-env`, `--allow-read` (optional fixture file). Deno Deploy grants net/env; fixture read is only for local/CI.

## Regenerating the tool catalog

After changing Wikipedia tool signatures or `TOOL_DESCRIPTIONS` in
`src/mcp_toolcall_lab/`:

```bash
python -m mcp_toolcall_lab.wikipedia_mcp_catalog
```

Commit the updated `catalog.generated.json`. CI compares it to live
`create_mcp().list_tools()` and to PR #23's recorded `POST /mcp` hop
shape (`jsonrpc`, SSE `event: message`, `Mcp-Session-Id`, protocol
`2025-06-18`).
