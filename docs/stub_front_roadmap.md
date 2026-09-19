# Stub front + richer MCP debug (slices ~#20–#30)

GitHub issues in this repo currently stop around #14. The slices below are
the intended follow-ups; file them as #20–#30 when ready. This PR lands the
first four so the rest have a working stub to hang on.

LibreChat / Open WebUI stay **clients under test**. The stdlib stub is a
**reference front** (same role as the Gradio/Streamlit lane in #14): dual
locators, `/c/{chat_id}`, heading→body, MCP case split. It is not a product
clone and not a CommonMark engine.

| Slice | Intent | Status |
| --- | --- | --- |
| ~#20 | MCP JSONL: `event`, `duration_ms`, `debug` (resolved `chat_id` + source). Auto-acquire from `_meta` → `X-Chat-Id` / `X-Conversation-Id` → per-session mint. | **this PR** |
| ~#21 | stdlib stub front: ATX heading → section body; LibreChat + OWUI locators; `/c/{chat_id}` | **this PR** |
| ~#22 | MCP pattern cases on the stub (`MCP_SUCCESS` / `EMPTY` / `ERROR` / `UNREACHABLE`) vs heading hit/miss | **this PR** |
| ~#23 | Pass `chat_id` naturally: URL, form, `_meta`, `X-Chat-Id` | **this PR** |
| ~#24 | README + Wikipedia-shaped markdown fixtures (ATX only) | **this PR** |
| ~#25 | Accuracy harness vs `myon-bioinformatics/markdown` (do **not** vendor a CommonMark parser here) | later |
| ~#26 | Playwright against the stub (reuse `frontends.py` locators; no product Docker) | later |
| ~#27 | Point stub at existing `openai_toolcall_mock` as an optional backend | later |
| ~#28 | Light CPU LLM in Docker as the test-time backend (compose service DNS) | later |
| ~#29 | Anti-pattern matrix for the stub (same catalog IDs as LibreChat smoke) | later |
| ~#30 | CI: stub + protocol on every PR; product UIs stay `chat-e2e` / smoke | later |

## One-liners (landed)

```bash
python -m mcp_toolcall_lab.stub_front turn --heading "Yokohama"
python -m mcp_toolcall_lab.stub_front turn --heading "Find municipalities named Yokohama"
python -m mcp_toolcall_lab.stub_front serve --port 8765
python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1
```
