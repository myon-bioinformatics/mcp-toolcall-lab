# Ironmate representative call

This slice connects the thin Ironmate MCP adapter to the lab's existing JSONL call trace.

`IronmateClient.call()` still delegates the actual operation to the MCP session. It does not implement repository search or other Ironmate business logic. When `MCP_TOOLCALL_LOG` is set, the wrapper records the same `tools/call` event shape used elsewhere in the lab, including arguments, outcome, duration, optional caller `_meta`, and optional debug correlation.

Both success and transport/tool exceptions are recorded. Exceptions are re-raised after the error trace is written.

The default tests remain offline and deterministic. The current representative tool name comes from the offline contract fixture introduced in #43; it is not evidence that a live Ironmate server currently advertises that exact tool. The live catalog refresh/drift-check follow-up remains tracked in #39.
