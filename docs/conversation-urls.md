# Conversation, agent and pull-request URL identities

`conversation_url.parse_reference(url)` is stdlib-only and offline. It returns
`provider`, `kind`, opaque `id`, and a canonical `url` without query/fragment,
or `None` for an unsupported/malformed URL. It performs no authentication or
content retrieval. These are observed route shapes, not service API guarantees.

| Service | Observed route | Kind |
| --- | --- | --- |
| ChatGPT | `https://chatgpt.com/c/{id}` | conversation_id |
| Gemini | `https://gemini.google.com/app/{id}` | conversation_id |
| Claude | `https://claude.ai/chat/{id}` | conversation_id |
| M365 Copilot | `https://m365.cloud.microsoft/chat/conversation/{id}` | conversation_id |
| GitHub Copilot | `https://github.com/copilot/c/{id}` | conversation_id |
| Cursor | `https://cursor.com/agents/bc-{id}` | agent_id |
| GitHub PR | `https://github.com/{owner}/{repo}/pull/{number}` | pull_request_id |

M365's example screenshot has a clipped ID. Tests use a synthetic full token;
the parser does not reconstruct unseen characters or prove that an ID exists.
No personal conversation IDs or contents are stored in fixtures.
Only the listed routes are supported; share links and alternate hostnames are
not inferred. IDs are case-preserving, bounded URL tokens, not forced UUIDs.
HTTPS, exact hosts and full path matches prevent prefix/domain lookalikes.

```sh
PYTHONPATH=src python -m mcp_toolcall_lab.conversation_url \
  'https://gemini.google.com/app/0123456789abcdef?hl=ja'
```

The JSON includes `content_fetched: false`. Exit 0 means a recognized identity,
not permission to read the conversation; unsupported inputs return exit 1.
`trace_probe --url` uses this parser for hosted services and PRs. Existing
self-hosted LibreChat/Open WebUI `/c/{id}`, message paths, `/s/{id}` and query
extraction remain the legacy fallback. That fallback identifies a URL shape,
not an authenticated service. `resume_chat_path()` remains a legacy `/c/`
helper; use the returned canonical URL to resume a hosted-service resource.
Trace clustering retains its existing same-ID-string behavior, so use the
provider and canonical URL from `parse_reference` when storing cross-service
identities; do not treat an opaque token as globally unique.

## Content access is a separate operation

Private chat URLs alone do not supply chat text. Reading content needs a
service-specific authorized API, user export, or a separately authorized
browser flow. This change adds none of those transports and no MCP tool.

GitHub PRs return `repository` and `number` (a decimal string), with the ID
`owner/repo#number` to avoid collisions across repositories. Reuse the existing
GitHub tools rather than scraping a PR as a chat. Current `gh_identity.pr()`
returns state/head/base identity, not the PR body or diff; `comments()` returns
previews. `gh_identity.issue()` reads Issue bodies but explicitly rejects PRs.
The lab's existing
`github_ops_server` wraps vendored `gh_ops` for status, comments and other
GitHub operations. Full diffs/files still require their specific GitHub
endpoints/tools; identity extraction alone does not claim full PR retrieval.

PR #110 concerns shared source access and saved HTML extraction, not chat URL
identity. This change is independent of its unmerged source-access foundation.
