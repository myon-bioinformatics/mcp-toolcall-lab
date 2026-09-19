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
- Shared mock helpers (`mcp_toolcall_lab.mock.common`: JSONL, id mints,
  chat headers, SSE close). MCP log schema and OpenAI `tool_calls`
  decision stay in their own files
- `dispatch_tool`, stdlib `McpStdlibSession`
- Serverless stub try: Actions boots `docker/stub-pages` (stub + MCP +
  CPU-class `cpu-llm`), JSONL anti-patterns, GitHub Pages static report
- Vendored `markdown.py` for heading→body / table / HTML on the stub
- Real tiny GGUF overlay (`docker-compose.gguf.yml`, opt-in via
  `stub-pages.yml`'s `use_real_gguf` input): auto-discovered + checksummed
  fetch script, `/v1/chat/completions` smoke check distinct from `/health`,
  `cpu_llm_backend` in the published summary

## Later (unnumbered)

- Accuracy harness vs `myon-bioinformatics/markdown` (do not vendor CommonMark here)
- Playwright against the stub (reuse `frontends.py`; no product Docker)
- Optional `openai_toolcall_mock` backend for the stub
- Playwright against the stub (reuse `frontends.py`; no product Docker)
- Pin `CPU_LLM_GGUF_SHA256` once a real `stub-pages` CI run (with real
  internet) has printed and verified the digest out of band

## One-liners

```bash
python -m mcp_toolcall_lab.stub_front turn --heading "Yokohama"
python -m mcp_toolcall_lab.stub_front serve --port 8765
python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1
```
