# Jev offline comparison benchmark

This benchmark is the next #39 evaluation slice after the Jev -> Ironmate execution path.

It compares two policies over the same mixed English/Japanese request set:

- **baseline**: the existing selector path is treated as one LLM call per request;
- **assisted**: the merged Jev policy may avoid that selector for high-confidence Ironmate and high-confidence no-tool cases.

The report keeps the existing #45 counters: wrong routes, policy fallbacks, execution fallbacks, out-of-scope families, split latency, and tool success. It adds baseline/assisted LLM-call counts and their delta.

## What the fixture proves

`fixtures/jev_assisted/benchmark_cases.json` contains realistic-shaped requests across Ironmate, Wikipedia, mock, and no-tool families, including Japanese and English. It exercises the complete policy and metric plumbing deterministically in default CI.

The probabilities in this file are **scenario labels**, not measured outputs from a live Jev/TypeSafe/OpenAI-compatible model. Therefore the fixture can prove that the benchmark arithmetic and fallback policy behave as intended, but it must not be cited as evidence that Jev really saves 50% of LLM calls in production.

A later live/authorized run can feed measured decisions into the same metric surface. Live model quality should be reported separately by `prob_source` and compared without rewriting these deterministic tests.


## Correctness and coverage scope

The top-level report includes `prob_sources`, so a serialized or displayed report still identifies the provenance of its decision probabilities even when separated from this document.

`correctness_regressions` is deliberately narrow: it counts non-fallback wrong routes, where Jev removed the selector-LLM safety net and selected the wrong family. Policy-fallback rows are excluded because the existing LLM flow remains in control; this offline harness does not claim to measure the correctness of that baseline flow.

This mixed fixture focuses on family distribution, language mix, high-confidence direct routes, no-tool skips, and low-confidence policy fallback. Ironmate execution failure and the `needs_tool_but_no_family` / `unknown_family` router branches remain covered by the dedicated router/assisted unit tests rather than being duplicated here.
