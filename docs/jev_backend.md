# `jev_backend` — backend-switch + trace skeleton (#36 / #37, step 1)

## What this is

The minimal "backend switch" skeleton #36/#37 call for, scoped to exactly
their P0 pair:

- **`fixture`** (`FixtureBackend`) — replays a canned answer recorded in a
  JSON file, keyed by question name. No HTTP client at all.
- **`typesafe_mock`** (`TypeSafeMockBackend`) — goes through the real
  `jev_typesafe` request-building/response-parsing path via
  `TypeSafeClient`, with an injected in-process `respond` callable standing
  in for the real transport. Proves the same request/response plumbing a
  live call would use, without a socket or `TYPESAFE_API_KEY`.

Both answer in TypeSafe's real `noul`/`choice`/`score` wire shape —
exactly what `jev_shim.validate_payload` checks — and tag every answer with
`prob_source` so a downstream consumer, or `jev_shim.brier_score()`, never
has to guess which kind of source produced a confidence.

### `prob_source` contract

`prob_source` is an extensible string identifier, not a closed enum. The
backends shipped in this slice use exactly `fixture` and `typesafe_mock`.
Future backends must choose a stable value that describes the source of the
probability/confidence rather than merely the transport. In particular,
self-reported generic-LLM confidence, logits-derived probability, and a
TypeSafe-provided probability must remain distinguishable; calibration or
Brier-score aggregation must not silently pool different `prob_source`
values. New values should therefore be documented when their backend lands.

`answer_with_trace()` times one `Backend.answer()` call and appends a
single JSONL row to the same `MCP_TOOLCALL_LOG`-gated trace file
`record.py` writes MCP tool calls to, under `event: "jev/backend_answer"` —
one trace file, multiple event kinds, same correlation context rather than a
second logging path.

### Trace minimum contract

A `jev/backend_answer` row keeps these fields stable for downstream
router/benchmark consumers:

- `event`: `"jev/backend_answer"`
- `backend`: backend implementation name
- `prob_source`: probability/confidence provenance described above
- `name` and `kind`: question identity and `noul`/`choice`/`score` kind
- `answer`: validated TypeSafe-shaped answer payload
- `duration_ms`: backend-answer latency measured with a monotonic clock

Correlation fields remain optional because callers may not have chat
context. When supplied, `meta` and `debug` are preserved; a
`debug.chat_id` is also exposed as top-level `chat_id`, matching the
existing trace convention. Additional fields may be added later without
removing or changing the meaning of the minimum fields above.

## What this is not (yet)

Per #37's backend priority (P0 `fixture`, P0 `typesafe_mock`, P1
`openai_compat`, P2 Open Jev / LocalJev, P3 TypeSafe live), this PR is only
the two P0 backends. Not included here, and intentionally deferred to
later, independently mergeable PRs:

- an `openai_compat` backend wired to the existing llama.cpp Docker path
  (GGUF stays opt-in, not part of default `pytest`),
- a real (live) `TypeSafeClient`-backed backend,
- the `jev_router` typed decision (`needs_tool` / `tool_family` /
  `confidence` / `prob_source`) from issue #39 — that consumes this
  backend-switch layer but lives in its own module and its own fixtures
  directory, not here (see #39's review discussion on why `jev_answerer`
  and `jev_shim` were deliberately decoupled — the same reasoning applies).
- confusion matrix / calibration / correctness-regression-gate reporting —
  those are #39's benchmark step, once a router exists to benchmark.

## Default CI

`fixtures/jev_backend/sample_answers.json` and `tests/test_jev_backend.py`
run under plain `pytest -q` — no network, no `TYPESAFE_API_KEY`, no opt-in
marker, matching this repo's "default pytest stays network-free"
convention (see `tests/conftest.py`, `docs/jev_shim.md`).

## Usage

```python
from mcp_toolcall_lab.jev_backend import DEFAULT_FIXTURE_DIR, FixtureBackend, TypeSafeMockBackend, answer_with_trace
from mcp_toolcall_lab.jev_typesafe import noul_question

fixture_backend = FixtureBackend.from_path(DEFAULT_FIXTURE_DIR / "sample_answers.json")
answer_with_trace(fixture_backend, "is_actionable", noul_question(), state="some state")

def respond(name, question, state):
    return {"type": "noul", "noul": 0.5}

mock_backend = TypeSafeMockBackend(respond=respond)
answer_with_trace(mock_backend, "is_ready", noul_question(), state="some state")
```
