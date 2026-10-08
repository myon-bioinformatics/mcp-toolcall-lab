# GitHub-ops MCP server (read-only v1)

`src/mcp_toolcall_lab/github_ops_server.py` is a separate FastMCP server that
exposes vendored [`gh_ops.py`](../vendor/gh_ops.py) — stdlib-only GitHub REST
operations from `myon-bioinformatics/browser-test-kit` (`scripts/gh_ops.py`,
[#9](https://github.com/myon-bioinformatics/browser-test-kit/issues/9),
[#10](https://github.com/myon-bioinformatics/browser-test-kit/pull/10),
merged as `dce1533`) — as MCP tools. Most tools are thin wrappers around
`gh_ops`. The exception is `resolve_public`: it is an intentionally small,
stdlib-only anonymous resolver owned by this lab because it combines GitHub
UI/API/raw URLs with GitHub Pages and records direct HTTP observations.

This is its own MCP endpoint, not a tool added to the existing
mock server in `server.py`/`catalog.py`. It is also not part of
`mcp_toolcall_lab.export`'s `openwebui_mcp_mock.py` / `librechat_mcp_mock.py`
generation: those two files are one product (the mock catalog), copied twice
under product-matched names, and inlining an unrelated GitHub client into
them would defeat that "exact same server, byte for byte" contract. If a
copyable standalone GitHub-ops file is ever needed, it should be its own
generator (mirroring `wikipedia_mcp_catalog.py`'s narrower export, not
`export.py`'s), not a branch added to the existing one. `mcp.add_middleware`
still reuses `server.ObservabilityMiddleware` as-is, so `tools/call` rows
land in the same `MCP_TOOLCALL_LOG` JSONL as every other server here when
that variable is set — best-effort: a middleware-API mismatch is swallowed,
never a reason the GitHub tools stop working.

No Ironmate/GitHub tool already existed in this repo:
[`ironmate_client.py`](../src/mcp_toolcall_lab/ironmate_client.py) is a
generic, business-logic-free adapter that only logs whatever MCP tool call a
caller hands it (see [`docs/ironmate_call.md`](ironmate_call.md)) — it is not
a GitHub client and has no `gh_ops`-shaped operations to align with, so this
is new surface, not a duplicate.

## Run it

```bash
pip install -e '.[test]'   # or: pip install "fastmcp>=3.4.8,<4"
export GITHUB_TOKEN=ghp_...   # or GH_TOKEN; omit for unauthenticated (low rate limit) reads
python -m mcp_toolcall_lab.github_ops_server
```

Streamable HTTP at `/mcp`, defaulting to `http://127.0.0.1:8010/mcp`
(`GITHUB_OPS_MCP_HOST` / `GITHUB_OPS_MCP_PORT` — deliberately not
`MCP_HOST`/`MCP_PORT`, so it can run alongside `server.py`'s mock on a
different port without either overriding the other's env vars).

```bash
fastmcp list http://127.0.0.1:8010/mcp --json
fastmcp call http://127.0.0.1:8010/mcp pr_status repo=myon-bioinformatics/mcp-toolcall-lab number=1 --json
```

## Auth

The token comes **only** from the `GITHUB_TOKEN` / `GH_TOKEN` environment
variables — exactly how `gh_ops.Client()` already resolves it when
constructed with no explicit token. No tool below takes a `token` argument,
returns one, or logs one; `tests/test_github_ops_server.py` asserts no tool's
schema has a `token` property. `_safe()` catches `gh_ops.GhOpsError`,
`ValueError`, and `KeyError`, scrubs their messages, and returns
`{"ok": False, "error": ...}`. An unexpected exception (for example, a
malformed successful HTTP response that violates the expected response shape)
is left as a FastMCP tool error rather than being reported as an ordinary
`ok: False` result. This keeps implementation defects visible; a tool error
does not terminate the server. Tokens in handled GitHub error text are scrubbed
even if a response body contains one.

## Tools (v1, read-only)

| Tool | Wraps | Notes |
| --- | --- | --- |
| `pr_status(repo, number)` | `gh_ops.pr_status` | One-line PR state, mergeability, head/base, sizes. |
| `pr_for_branch(repo, branch, state="all")` | `gh_ops.pr_for_branch` | `ok` is False when no PR has that head. |
| `open_prs(owner, org=False, repos=[], limit=50)` | `gh_ops.open_prs` | Search-based by default; `repos=[...]` reads each repo's own `/pulls` instead, for tokens/proxies without search access. `limit` is 1–100. |
| `issue_comments_digest(repo, number, since=None, last=None, author=None, preview=110, show=[])` | `gh_ops.issue_comments` | Preview rows for every comment; full bodies **only** for the indexes listed in `show`. The `save`-to-file option is not exposed — this server has no reason to write to the host filesystem. `last` is 1–100 when set; `preview` is 1–1000 characters. |
| `check_runs(repo, sha, min_checks=1)` | `gh_ops._check_runs` + `gh_ops._summarize_checks` | A single non-waiting read (total/pending/failed/succeeded + per-run detail). Unlike `gh_ops`'s own `checks-wait` CLI command, this never polls or sleeps inside the tool call. `min_checks` is 1–100. |
| `workflow_runs(repo, sha=None, workflow=None, limit=30)` | `gh_ops.runs` | Exactly one of `sha`/`workflow` must be set (enforced by `gh_ops.runs` itself). `limit` is 1–100. |
| `url_pr(repo, number, tab=None)` | `gh_ops.url_pr` | Pure string building; no network. |
| `url_compare(repo, base, head)` | `gh_ops.url_compare` | Pure string building; no network. |
| `url_blame(repo, ref, path, line=None)` | `gh_ops.url_blame` | Pure string building; no network. |
| `url_history(repo, ref, path)` | `gh_ops.url_history` | Pure string building; no network. |
| `url_runs(repo, workflow=None, branch=None, event=None, status=None)` | `gh_ops.url_runs` | Pure string building; no network. |
| `url_search(query, kind="code")` | `gh_ops.url_search` | Pure string building; no network. |
| `url_raw(repo, ref, path)` | `gh_ops.url_raw` | Pure string building; no network. |
| `resolve_public(repo, ref="main", path=None, probe=True)` | lab public resolver | Builds repository/API/Pages and optional raw/contents candidates. With `probe=True`, performs anonymous HTTPS observations only and distinguishes reachable/not-found/auth-required/rate-limited/unverified states. No token argument. |

## Out of scope for v1 (follow-ups)

`gh_ops.py` also has write operations and one blocking read, none of them
wired up here:

- **Writes**: `pr_merge`, `pr_body_replace`, `pr_body_set`, `pr_edit`,
  `workflow_dispatch`, `file_put`, `sync_main` (git, not REST). Any of these
  as an MCP tool needs its own review of confirmation/dry-run UX before a
  model can trigger a real GitHub mutation.
- **`checks_wait`**: polls with `time.sleep` until checks complete or a
  timeout; exposing it as an MCP tool would block the calling turn for up to
  its `timeout` (default 600s), which the task's "never block for minutes
  inside a tool" rule rules out for v1. `check_runs` above covers the
  same read without the wait loop.
- **`workflow_state`** and the offline **`comments_file`** digest were not
  requested for v1; both are thin enough to add later if needed.

## Provenance / refresh

`vendor/gh_ops.py` is a byte-identical snapshot of
`scripts/gh_ops.py` at commit
[`dce15333d100d1163b1b706d8e8de769a1e116be`](https://github.com/myon-bioinformatics/browser-test-kit/commit/dce15333d100d1163b1b706d8e8de769a1e116be),
the squash-merge of browser-test-kit PR
[#10](https://github.com/myon-bioinformatics/browser-test-kit/pull/10) on `main`
(the file is byte-identical to the PR head `679c05e` it was first vendored from).
`vendor/gh_ops.provenance.json` records that
commit, its git blob SHA, and the file's SHA-256; `tests/test_gh_ops_provenance.py`
fails the build if the vendored file drifts from that record. See
[`vendor/README.md`](../vendor/README.md) for the refresh command — the same
shape as the existing `markdown.py` refresh, pointed at
`myon-bioinformatics/browser-test-kit` instead. Pin a commit, not `main` itself,
so a later unrelated push to `main` can't silently change what this repo vendors.

## Tests

```bash
pytest -q tests/test_github_ops_server.py tests/test_gh_ops_provenance.py
```

No network: every REST-backed tool test stubs `gh_ops.Client`'s transport
(the same `Stub`/`client_for`/`reply` shape as
`browser-test-kit`'s `tests/python/test_gh_ops.py`) and calls the tool
through FastMCP's in-memory `Client(transport=mcp)`, the same in-process
pattern `tests/test_schema.py` uses via `mcp.list_tools()`. Coverage
includes each tool, the URL builders' exact strings, a stubbed 403 surfacing
as `ok: False` rather than a raised exception, and that no tool argument or
result ever contains a token. The MCP input schemas publish numeric bounds and
reject values outside them before calling GitHub. A malformed successful
response is also tested to remain a protocol-level tool error while a later
valid call still succeeds.
