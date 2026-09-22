# Jev-assisted Ironmate routing

This is the first execution slice that connects the typed Jev router to the thin Ironmate MCP client.

The optimization is intentionally narrow:

- high-confidence `ironmate` -> call Ironmate directly and skip the heavier LLM selector;
- high-confidence `needs_tool=false` -> skip both LLM selection and tool execution;
- low-confidence or contradictory decisions -> preserve the caller-supplied existing LLM fallback;
- `wikipedia` and `mock` still use that existing flow in this slice rather than expanding the pre-router all at once.

The executor does not know Ironmate business logic. The caller supplies the MCP tool name and arguments, and `IronmateClient` performs the normal MCP call.

## Offline comparison

`benchmark_row()` and `benchmark_summary()` expose the #39 measurements without external credentials: LLM calls avoided, wrong routes, policy/uncertainty fallbacks, confident-but-not-yet-wired out-of-scope families, split decision/LLM/tool latency, and tool success/attempt counts.

The benchmark treats fewer LLM calls as useful only alongside the independent wrong-route count. A later live/authorized benchmark can feed the same row shape without changing deterministic default CI.

The Jev wire contract remains defined by #36 and #37; this module consumes `RouteDecision` from the existing router and does not redefine Jev payloads.


## Trace correlation

The Jev decision row and Ironmate caller row receive the caller's same `meta`/`debug` objects. Cross-layer correlation is therefore explicit rather than invented by this adapter: pass a shared identifier such as `debug["chat_id"]` (and/or a trace id in `meta`) when the two rows need to be joined. The Ironmate caller trace uses `source="jev-router"` unless the caller explicitly supplies another source.
