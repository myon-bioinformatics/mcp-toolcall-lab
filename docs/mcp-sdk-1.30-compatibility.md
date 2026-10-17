# MCP SDK 1.30.0 compatibility audit

Recorded: 2026-10-17

## Scope and dependency ownership

PR [#131](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/131)
updated only the test extra's official `mcp` SDK pin from 1.28.1 to 1.30.0.
It was already merged as `a5f49c559d0a883f7d6a8728d63faf4412fbc42c`
when this follow-up audit started.

The application imports `FastMCP` from the independent `fastmcp` package,
not `mcp.server.fastmcp`. Its production requirement stays
`fastmcp>=3.4.8,<4`; the tested installation was FastMCP 3.4.9 with
MCP SDK 1.30.0. FastMCP's server/client extras accept `mcp>=1.24.0,<2.0`,
so the test pin is compatible with its dependency range.

These packages are distinct, but their HTTP session handling is connected:
FastMCP 3.4.9's `FastMCPStreamableHTTPSessionManager` subclasses the official
SDK's `StreamableHTTPSessionManager` and calls its constructor without
overriding idle timeout or capacity. Consequently the SDK's 1,800-second
idle timeout and 10,000-session limit **also apply to this stateful lab server**.
The independent FastMCP constructor does not expose the identically named
settings from the official SDK's `FastMCP`; no such arguments were added here.

## Observed boundaries

`tests/test_sdk_transport_boundaries.py` uses the application's real
`create_mcp().http_app()` over loopback TCP and the unmodified chat/direct
callers. No external service, new dependency, or production setting is needed.

| Boundary | Observed result |
| --- | --- |
| Same-origin HTTP 307 | Both chat and direct callers finish the tool call; the chat tool-role message keeps its call ID. |
| HTTP 307 to another hostname or port | Both callers fail with an `HTTPStatusError` inside the SDK's task-group exception; the target receives no request. |
| Stateful SDK session with open GET stream | Session ID and tool calls remain valid beyond the accelerated idle interval. |
| POST-only idle session | The old session ID returns HTTP 404; a fresh chat turn initializes and calls successfully. |
| Server-deleted SDK session | The next tool call raises `McpError: Session terminated`; a fresh direct connection succeeds. |
| Session capacity reached | A new chat connection fails with HTTP 503; the existing session still works, and closing it frees capacity. |

Idle/capacity tests retain the real inherited session manager and override its
limits only inside test fixtures. The tests assert the original defaults first.
Existing protocol/trace tests additionally cover initialize, tools/list,
tools/call, unknown tools, argument validation, empty results, timeout,
and the Open WebUI/LibreChat standalone server entry points.

`chat_sim.send_via_chat()` and `send_direct()` initialize a new connection on
every invocation. They neither cache expired sessions nor silently retry tool
calls. A caller retaining a lower-level `ClientSession` must open and initialize
a fresh session after termination; the existing behavior remains unchanged.
Endpoints should use their final origin rather than depend on cross-origin
redirects. No OAuth provider or legacy SSE client is configured in this path,
so no OAuth/SSE-specific application changes were made.

## Validation and UI evidence limits

- Python 3.11.17: full suite **923 passed, 21 skipped, 53 subtests passed**.
  Optional browser/UI/runtime tests were skipped in this local environment.
- Python 3.14.8: **10 boundary tests passed**, plus **13 existing protocol/trace
  tests passed**. This is focused coverage, not a full 3.14 suite claim.
- The package's Python `>=3.11` requirement and the separate vendorable public
  resolver's Python 3.9/3.10 contract are unchanged.
- Original PR head `867aa23d83f1808c44e94843ae12c715383421cc`:
  [test](https://github.com/myon-bioinformatics/mcp-toolcall-lab/actions/runs/38988002475),
  [LibreChat smoke](https://github.com/myon-bioinformatics/mcp-toolcall-lab/actions/runs/38988002465),
  and [Galleria](https://github.com/myon-bioinformatics/mcp-toolcall-lab/actions/runs/38988002185)
  succeeded. The LibreChat Docker build installed MCP 1.30.0 and FastMCP 3.4.9.
- The LibreChat artifact's `antipatterns.jsonl` records `ANTIPATTERN /
  MCP_PICKER_OFF`: the completion request had no tools and no MCP tool call
  was recorded. Its green smoke run is therefore **not evidence of a successful
  end-to-end LibreChat MCP call**. This audit does not attribute that UI
  observation to the SDK update.
- Open WebUI's Docker workflow did not run for the dependency-only PR because
  of its path filter. Its full browser loop was not rerun locally; the shared
  server protocol and chat-message contracts are covered separately above.

No SDK compatibility blocker was reproduced in the tested transport paths.
Production code, authentication, retry policy, server defaults and UI flows
are unchanged.

Upstream behavior reference:
[official SDK v1.30.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0).

Focused reproduction after `pip install -e '.[test]'`:

```sh
python -m pytest -q tests/test_sdk_transport_boundaries.py tests/test_streamable_http_protocol.py tests/test_trace.py
```
