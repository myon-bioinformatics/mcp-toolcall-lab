# Wikipedia MCP (Deno)

Streamable HTTP MCP for `fetch_wikipedia_article` /
`fetch_wikipedia_section` only. See [`docs/wikipedia_mcp_deploy.md`](../../docs/wikipedia_mcp_deploy.md).

On Deno Deploy set `MCP_SESSION_SECRET`. CORS defaults to `*`; this is a
public Wikipedia proxy unless you also set `MCP_ALLOWED_ORIGIN`.

```bash
deno task start
deno task test
```
