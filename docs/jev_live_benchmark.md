# Measured Jev backend routing benchmark

This is the opt-in/live counterpart to the deterministic scenario benchmark from #46.

`run_backend_routing_benchmark()` accepts any existing `Backend`. With `OpenAICompatBackend` plus the provided stdlib `urllib_json_post`, it can measure the repository's opt-in llama.cpp/OpenAI-compatible path. A future authorized TypeSafe backend can use the same runner.

The runner deliberately measures **routing only**. It does not execute Ironmate, Wikipedia, or mock MCP tools. Therefore:

- `wrong_routes` is measured against the labeled expected family;
- `llm_selector_calls_avoided_estimate` means a confident non-fallback route could skip the existing selector LLM;
- it is an estimate, not a measured end-to-end LLM-call reduction;
- tool success and MCP latency belong to the execution benchmark, not this report;
- calibration remains separated by `prob_source`.

Default pytest remains offline. Network access happens only when a caller explicitly constructs a network-backed backend/post function. No API key or live endpoint is required by CI.

Example for the existing opt-in llama.cpp service:

```python
from functools import partial
from mcp_toolcall_lab.jev_live_benchmark import run_backend_routing_benchmark, urllib_json_post
from mcp_toolcall_lab.jev_openai_compat import OpenAICompatBackend

backend = OpenAICompatBackend(
    base_url="http://cpu-llm:8080",
    model="your-opt-in-model",
    post=partial(urllib_json_post, timeout=60.0),
)
report = run_backend_routing_benchmark(cases, backend)
```

Do not combine `self_reported`, fixture, TypeSafe-native, or future logits-derived calibration values unless an explicit calibration step justifies doing so.
