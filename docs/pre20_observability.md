# Pre-#20 technical cleanup and observability contract

This branch intentionally prepares the lab for the later #20-#30 lightweight
Markdown/Python frontend work without reserving a large product-specific
architecture in advance.

## Correlation rule

Never invent an Open WebUI or LibreChat chat id.

A frontend may provide a real `chat_id` / `conversation_id` through MCP
`_meta` when available. Harnesses may also bridge a naturally acquired id
through environment variables such as `MCP_CHAT_ID`.

When no product id exists, the lab creates only its own:

- `trace_id`: joins a logical flow across boundaries.
- `request_id`: identifies one request.
- `event_id`: identifies one JSONL record.

This means every MCP call is debuggable while preserving the distinction
between observed product ids and lab-generated ids.

## Logging contract

`MCP_TOOLCALL_LOG` rows are versioned with `schema_version: 2`.
They always contain `event_id`, `meta.trace_id`, and `meta.request_id`.
Known frontend/model ids are carried through rather than renamed.

Future UI-specific probes should acquire ids at natural boundaries:

1. route change / conversation URL after Send;
2. frontend API response containing a conversation/chat id;
3. model response/tool-call ids;
4. explicit MCP `_meta` when the client supports it.

## Why this is done before #20

The later lightweight stub should be able to use only Python stdlib for its
HTTP/UI shell and Markdown fixtures, while reusing the same trace semantics.
Gradio/Streamlit/Open WebUI/LibreChat remain adapters or systems under test,
not sources of truth for correlation.

## #20-#30 direction

Planned experiments can build on this contract:

- stdlib HTTP frontend serving Markdown-derived prompt fixtures;
- heading -> chat-body deterministic stub behavior;
- README/Wikipedia-like Markdown fixtures for parser/reverse-engineering tests;
- richer MCP success/empty/error/timeout/schema-mismatch patterns;
- optional Docker-only tiny CPU LLM backend as a later integration lane.

Heavy UI frameworks and the CPU model should stay optional so default tests
remain dependency-light.
