# Wikipedia MCP (Deno)

Keyless Streamable HTTP MCP for `fetch_wikipedia_article` /
`fetch_wikipedia_section` only. Sessions are Deno KV (TTL + cap), not an
in-memory `Set` and not an HMAC secret. See
[`docs/wikipedia_mcp_deploy.md`](../../docs/wikipedia_mcp_deploy.md) for
CORS/rate-limit warnings and Deploy steps.

```bash
deno task start
deno task test
```
