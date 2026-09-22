# Jev-assisted Ironmate routing

This is the first execution slice that connects the typed Jev router to the thin Ironmate MCP client.

The optimization is intentionally narrow:

- high-confidence `ironmate` -> call Ironmate directly and skip the heavier LLM selector;
- high-confidence `needs_tool=false` -> skip both LLM selection and tool execution;
- low-confidence or contradictory decisions -> preserve the caller-supplied existing LLM fallback;
- `wikipedia` and `mock` still use that existing flow in this slice rather than expanding the pre-router all at once.

The executor does not know Ironmate business logic. The caller supplies the MCP tool name and arguments, and `IronmateClient` performs the normal MCP call.

## Offline comparison

`benchmark_row()` and `benchmark_summary()` expose the #39 measurements without external credentials: LLM calls avoided, wrong routes, fallback count, split decision/LLM/tool latency, and tool success/attempt counts.

The benchmark treats fewer LLM calls as useful only alongside the independent wrong-route count. A later live/authorized benchmark can feed the same row shape without changing deterministic default CI.

The Jev wire contract remains defined by #36 and #37; this module consumes `RouteDecision` from the existing router and does not redefine Jev payloads.
