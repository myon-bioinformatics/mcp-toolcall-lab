# History growth telemetry (Issue #115)

The OpenAI mock measures the complete incoming `messages` array before creating
its body-free wire projection. It persists numeric telemetry only:

- `message_json_chars`: Unicode code points in
  `json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`.
  This includes keys, punctuation, roles, tool names/IDs, arguments, tool results,
  and any additional message fields. Format: `unicode_sorted_compact_v1`.
- `tool_call_argument_chars`: sum of code points in every
  `tool_calls[].function.arguments` string (all calls, including empty strings).
  Arguments are opaque data: no parsing, execution or promotion into instructions.
- `content_chars`, `role_content_chars`, `tool_content_chars`: text-body breakdowns.
- `projection_json_chars`: size of the existing body-free projection only.
  This is not the complete history size.

The serialized messages exist transiently for counting and are never logged.
Prompt, argument, tool-result and authorization bodies are not retained. Existing
completion/chat IDs and per-chat turn indexing remain available for joining rows.
Characters are not UTF-8 bytes, tokens, context-window utilization, or original
HTTP payload size: JSON whitespace and key order are normalized.

Malformed message/content/tool-call structures return `invalid_messages` from
the metrics helper and receive HTTP 400 at mock ingress. Function arguments must
be strings; malformed JSON *inside* a string still counts as opaque text.
Unsupported multimodal content remains rejected rather than estimated.

`python -m mcp_toolcall_lab.history_growth "$OPENAI_MOCK_LOG"` reports only
validated telemetry. Projection-only legacy rows retain
`content_measurement: unavailable_in_wire_projection`; older content-only rows
can keep their valid body breakdown. Both have null total/argument counts and
`message_measurement: unavailable_in_legacy_log`. Invalid new metrics have null
total/argument counts and `message_measurement: invalid_metrics`.

This instruments mock request ingress. It does not connect a production proxy,
change compression or provenance/trust boundaries, or measure real GGUF inference
quality, tokens, context limits or latency. Those require separate evaluation.
