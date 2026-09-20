# `jev_shim` — offline calibration study for a prompted structured-judgment interface

## What this is not

This module has **no relationship to TypeSafe or its "Jev" model**, does not call
any external API, and makes no claim about how that (or any) real product works
internally. The name exists only for continuity with the conversation that
motivated this experiment: a third-party model publicly described as answering
only three schema-constrained primitives (a yes/no probability, a weighted
score + distribution, or a classification + distribution) rather than
generating free text, with the claim that this makes "hallucination
mathematically impossible" at the *output-shape* level.

## What this is

A narrower, answerable question: if you *prompt* an ordinary generative LLM to
answer in that same three-shape vocabulary, and *validate* its output against
the shape, how well does its self-reported confidence match reality? That's a
statement about **prompting + validation on top of a generic model**, not
about any particular product's internals.

Same philosophy as `prompt_experiment.py`: everything here is fixture-replay.
A fixture records a hypothetical model completion (as if a real LLM had been
prompted this way) and the real-world ground truth; `jev_shim.replay_case`
judges the recorded completion offline. **Nothing here opens a socket, calls a
model, or requires an API key.** Actually generating new completions against a
real backend (llama.cpp with grammar/JSON-schema-constrained decoding, an
OpenAI-compatible `response_format`, etc.) is a distinct, not-yet-built
follow-up — this module only establishes the judging/scoring half.

## The three primitives

| Kind | Input | Required response shape |
| --- | --- | --- |
| `noul` | a yes/no proposition + `state` | `{"probability": 0.0-1.0}` |
| `score` | a rubric + `state` | `{"score": number, "distribution": {label: prob}, "confidence": 0.0-1.0}` |
| `choice` | options + `state` | `{"choice": str (must be a distribution key), "distribution": {option: prob}, "confidence": 0.0-1.0}` |

A response is **schema-valid** only if it parses as JSON and matches its
kind's shape exactly — no missing or extra top-level keys, right types,
probabilities in `[0, 1]`, a `distribution` summing to ~1.0. Free text, or
JSON missing a required field, is schema-invalid. This is exactly the failure
mode real constrained decoding (grammar/JSON-schema-constrained sampling)
would prevent structurally — and that this offline harness cannot, since it
only judges a recorded string, never a live model. The `noul_malformed_output`
fixture exists specifically to show that failure mode and how it's scored
(excluded from calibration, not silently treated as a wrong-but-valid answer).

## Calibration, not just pass/fail

`calibration_summary()` computes the [Brier score](https://en.wikipedia.org/wiki/Brier_score)
over schema-valid `noul` cases: mean squared error between the declared
probability and the 0/1 ground truth. `0.0` is a perfect forecaster, `0.25` is
what an uninformative "always 0.5" forecaster scores, `1.0` is a maximally
confident, maximally wrong one. This is the actual point of the exercise: a
schema-valid response can still be a *badly calibrated* one (see
`noul_overconfident_wrong.json`, a 0.97 confident call that was wrong) — the
"hallucination is impossible" framing is a claim about output validity, not
about whether a generic prompted LLM's declared confidence is trustworthy.

## Usage

```bash
python -m mcp_toolcall_lab.jev_shim replay --out test-results/jev-shim.jsonl
```

Fixtures live in `fixtures/jev_shim/*.json`. Each has an `id`, `kind`, `state`
plus the kind-specific input (`proposition` / `criteria` / `options`), a
`raw_completion` (the recorded string), a `ground_truth`, and an `expected`
block asserting a subset of the audit fields (same convention as
`prompt_experiment.py`'s fixtures).

## Non-goals (explicit)

- Not a client for any real "Jev"-branded API, and not a benchmark of one.
- Not a live-model integration (no llama.cpp / OpenAI call exists yet).
- Not full JSON Schema — validation is hand-written and shape-specific,
  matching this repo's existing style (see `prompt_experiment.py`'s
  `raw_schema_valid`) rather than adding a `jsonschema` dependency.
