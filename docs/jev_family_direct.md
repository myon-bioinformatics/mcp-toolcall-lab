# Direct routing for Wikipedia and mock families

This extends #45's narrow Ironmate optimization without coupling the Jev router to tool implementations.

For high-confidence `wikipedia` or `mock` decisions, the caller may provide a zero-argument callable in `direct_calls`. If present, the selector LLM is skipped and that callable executes directly. If absent, the existing LLM flow remains unchanged.

The executor intentionally accepts direct calls only for the already-defined `wikipedia` and `mock` families. Ironmate continues through `IronmateClient` so its caller-side trace contract is preserved.

A direct-call exception is not a new hard failure boundary: execution falls back to the existing flow and records `wikipedia_call_error` or `mock_call_error` as `execution_fallback_reason`.

The callables are injected rather than importing `wikipedia_tool` or `catalog.dispatch_tool` here. This keeps routing independent from transport/tool business logic and lets callers choose real MCP, deterministic mock, or another existing boundary. Default pytest remains offline.
