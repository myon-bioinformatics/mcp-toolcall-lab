# Jev router contract

The router is a transport-independent consumer of the backend interface from
`jev_backend`. It does not dispatch tools. It only decides whether a request
is safe to pre-route or should fall back to the existing LLM path.

## Decision fields

`needs_tool` is the boolean interpretation of the `noul` answer
(`noul >= 0.5`). `tool_family` is one of `ironmate`, `wikipedia`,
`mock`, or `none`.

`none` means **no tool is required**. It is not an "unknown" or "cannot
decide" sentinel. When the first stage says a tool is needed but the second
stage chooses `none`, the decision records
`fallback_reason=needs_tool_but_no_family` instead of silently treating that
contradiction as "no tool".

`confidence` means the router's confidence in the **whole pre-route
decision**, not a calibrated probability that routing is correct. For a
no-tool decision it is `max(noul, 1-noul)`. For a tool route it is the
minimum of that first-stage certainty and the second-stage choice
`confidence`. The minimum is intentionally conservative: both gates must be
confident for a direct route. Its provenance is always carried separately as
`prob_source`; calibration must not pool different sources.

If the combined confidence is below `threshold` (default 0.75), the router
records `fallback_reason=low_confidence`. The consumer should then delegate
to the existing LLM rather than dispatch the proposed family directly.

## Fallback and confusion matrix

The four top-level buckets are:

- `routed-correct`: no fallback and selected family equals the fixture truth.
- `routed-wrong`: no fallback and selected family differs from fixture truth.
- `fallback-correct`: fallback occurred and the proposed family nevertheless
  equals fixture truth.
- `fallback-wrong`: fallback occurred and the proposed family differs from
  fixture truth.

These names measure the proposal at the point fallback was selected; they do
**not** claim the downstream fallback LLM itself was correct. Keep
`fallback_reason` on each row so analysis can further split
`low_confidence`, `needs_tool_but_no_family`, and future failure reasons.
Backend failures are not currently converted into route decisions; adding a
`backend_failure` fallback is a later explicit change rather than an implicit
exception swallow.

## Latency

`decision_ms`, `llm_ms`, and `tool_ms` remain separate. The router owns
only `decision_ms`; consumers/benchmarks may attach fallback-LLM and tool
latencies later. `latency_summary` reports them separately rather than
combining them.
