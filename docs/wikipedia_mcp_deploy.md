# Wikipedia-only Streamable HTTP MCP (Deno Deploy)

Keyless MCP endpoint that advertises **only** `fetch_wikipedia_article` and
`fetch_wikipedia_section`. Pages `#wiki` is **not** wired to this server in
this change; that connection (and any OpenAI `tools` / `tool_calls` → MCP →
`role: tool` browser loop) is a follow-up.

No MLIT, no API keys, no custom JSON event rows. The wire is official MCP
Streamable HTTP: JSON-RPC 2.0 on `POST /mcp`, SSE `event: message` for
responses, empty **202** for `notifications/initialized`, `Mcp-Session-Id`,
and CORS so a later github.io client can `fetch()` it.

`GET /mcp` (SSE resume / `Last-Event-ID`) is **not** implemented. Only
`POST /mcp` and `OPTIONS /mcp` (CORS preflight) are MCP. `GET /health` is a
deploy probe, not an MCP method.

Tool `inputSchema` is generated from the Python FastMCP catalog
(`python -m mcp_toolcall_lab.wikipedia_mcp_catalog` →
`catalog.generated.json`). Do not edit that JSON by hand.

## Public proxy, CORS, and rate limits

This process is an **unauthenticated Wikipedia proxy** when it is reachable
on the public internet. Default CORS is `Access-Control-Allow-Origin: *`
(no cookie/credential gate). Anyone who can `POST /mcp` can ask this server
to fetch MediaWiki extracts.

Mitigations that are on by default:

- `lang` must be a MediaWiki project code (`/^[a-z0-9-]{2,24}$/i`).
  Host injection (`evil.com/`, `#`, `@`) is rejected **before** `fetch`.
  The request URL hostname must be `{lang}.wikipedia.org`.
- Sessions are **HMAC-signed short-TTL tokens** (not an isolate-local `Set`
  and not Deno KV). Verification is signature + expiry only, so Deno Deploy
  isolate routing cannot drop a valid `Mcp-Session-Id`.
- Wikipedia `tools/call` is rate-limited per client IP per minute (in-process
  counter; best-effort per isolate).

Optional env:

| Env | Default | Role |
| --- | --- | --- |
| `MCP_SESSION_SECRET` | process-local random | **Required on Deno Deploy.** HMAC key shared by every isolate. If unset, a random secret is derived once per process (local/dev/tests only — tokens will not verify on another isolate). Never hardcode a production secret in source. |
| `MCP_SESSION_TTL_SECONDS` | `3600` | Session token lifetime. |
| `MCP_CORS_ORIGIN` / `MCP_ALLOWED_ORIGIN` | `*` | If set to a single origin (e.g. `https://myon-bioinformatics.github.io`), browsers from other sites cannot use the credential-less CORS grant. |
| `MCP_WIKI_FETCH_LIMIT_PER_MINUTE` | `30` | Per-IP Wikipedia fetch cap. `0` disables the limiter. |
| `MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE` | unset | Serve a vendored MediaWiki JSON instead of calling Wikipedia. |
| `MCP_TOOLCALL_LAB_WIKI_CACHE_TTL` | `300` | In-process extract cache TTL (seconds). |
| `MCP_TOOLCALL_LAB_WIKI_CACHE_MAXSIZE` | `16` | In-process extract cache cap. |

This is still a lab demo, not a production Wikimedia gateway. Do not point
untrusted traffic at it without tightening `MCP_CORS_ORIGIN` and the fetch
limit.

## Run locally

Deno 2.x (no npm install):

```bash
cd deploy/wikipedia-mcp
deno task start
# POST http://127.0.0.1:8000/mcp
```

No `--unstable-kv`. Optional: `MCP_HOST`, `MCP_PORT` / `PORT`,
`MCP_SESSION_SECRET`, and
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
3. **Set `MCP_SESSION_SECRET`** to a long random value shared by every isolate.
   Optionally set `MCP_ALLOWED_ORIGIN` / `MCP_CORS_ORIGIN`. Wikimedia needs a
   `User-Agent`; the server sends a lab UA on the server-side fetch.
4. After deploy, the public URL is `https://<project>.deno.dev/mcp`.

Permissions used at runtime: `--allow-net` (listen + Wikipedia), `--allow-env`,
`--allow-read` (optional fixture file). Deno Deploy grants net/env; fixture read
is only for local/CI. **`--unstable-kv` is not required.**

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
