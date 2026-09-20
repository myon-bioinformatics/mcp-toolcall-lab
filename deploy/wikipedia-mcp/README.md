# Wikipedia MCP (Deno)

Keyless Streamable HTTP MCP for `fetch_wikipedia_article` /
`fetch_wikipedia_section` only. Sessions are HMAC-signed short-TTL tokens
(not Deno KV and not an in-memory `Set`). On Deno Deploy set
`MCP_SESSION_SECRET`. See
[`docs/wikipedia_mcp_deploy.md`](../../docs/wikipedia_mcp_deploy.md) for
CORS/rate-limit warnings and Deploy steps.

```bash
deno task start
deno task test
```
