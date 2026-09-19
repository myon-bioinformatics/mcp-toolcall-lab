# Stub front and later slices (do not pre-number)

Do **not** reserve GitHub issue numbers for this work. File an issue when a
slice actually starts. Landed pieces live on the current branch; the rest
waits until someone picks it up.

LibreChat / Open WebUI stay **clients under test**. The stdlib stub is a
**reference front**: dual locators, `/c/{chat_id}`, heading→body, MCP case
split. It is not a product clone and not a CommonMark engine.

## Already in the tree

- MCP JSONL `debug` + auto `chat_id` (`_meta` → `X-Chat-Id` → session → mint)
- stdlib stub front (ATX heading → section body; LibreChat + OWUI locators)
- MCP pattern table (`SUCCESS` / `EMPTY` / `ERROR` / `UNREACHABLE` vs heading)
- `chat_id` on URL, form, `_meta`, and `X-Chat-Id`
- README-shaped + Wikipedia-shaped ATX fixtures
- Shared mints (`record.new_chat_id` / `new_call_id`), `dispatch_tool`,
  stdlib `McpStdlibSession`, JSONL reader

## Later (unnumbered)

- Accuracy harness vs `myon-bioinformatics/markdown` (do not vendor CommonMark here)
- Playwright against the stub (reuse `frontends.py`; no product Docker)
- Optional `openai_toolcall_mock` backend for the stub
- Light CPU LLM in Docker as a test-time backend (compose service DNS)
- Stub anti-pattern matrix (same catalog IDs as LibreChat smoke)
- CI: stub + protocol on every PR; product UIs stay smoke / `chat-e2e`

## One-liners

```bash
python -m mcp_toolcall_lab.stub_front turn --heading "Yokohama"
python -m mcp_toolcall_lab.stub_front serve --port 8765
python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1
```
