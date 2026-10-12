# Safe history compaction prototype (Issue #115)

This prototype is not a deployed proxy. No API keys or paid calls are used.

## Trust boundary

- The compaction core is in src/mcp_toolcall_lab/compaction.py.
- Tool results are **untrusted** even when they contain Markdown, fake role
  declarations, XML-like delimiters, JSON with a role field, or line breaks.
- Tool result content is **never promoted into a user or system message**.
  Instead, the caller receives an **out-of-band, typed provenance** record.
  Do not paste or interpolate its content into model-facing instructions.
- Provenance separates user_messages, assistant_decisions, tool_calls, and
  tool_results. Results retain the tool_call_id, source_role="tool",
  trust="untrusted", and original content.
- Malformed/orphan/duplicate/incomplete tool exchanges in the removed
  prefix fail closed. The safe cut never separates a tool call and its
  result inside the remaining suffix.
- If the removed prefix contains earlier user messages, compaction is
  declined rather than silently deleting constraints from the user.

## API

    from mcp_toolcall_lab.compaction import compact

    result = compact(messages, budget=2048, keep=6)
    send_to_model = result.messages
    # Keep result.provenance out-of-band; never turn it into a model instruction.

If the estimate is already within budget, the original list is returned
unchanged by identity. Budget is a *trigger*, not an enforced upper bound:
the prototype intentionally declines unsafe reductions, even when the
input is longer than the requested budget.

## Verification

- tests/test_compaction.py contains 28 tests covering trust boundaries,
  pairing, tool-result attacks, and fail-closed behavior. Attack inputs
  include actual line breaks, not only a literal backslash followed by n.
- tests/test_compaction_tool_choice.py compares tool name and arguments
  between original and compacted requests on five attack payloads and
  three real user intents. It runs the repo's deterministic CPU-lite
  stand-in (demos/cpu_llm_lite.py). Generated tool_call_id values are
  ignored because IDs are not decisions.
- These checks do **not** establish prompt-injection immunity for a
  generative model. Real GGUF inference, run-to-run variation, and
  anti-pattern-rate comparisons remain to be measured separately.

## Deferred Issue #115 scope

There is no compaction proxy/compose overlay, OPENAI_MOCK_LOG request
growth report, llama.cpp tokenizer integration, or real-model benchmark.
The core often declines normal multi-turn user histories because user
constraint-preserving summary rendering has not been implemented.
