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
`prob_source` (`"fixture"` or `"typesafe_mock"`) so a downstream consumer,
or `jev_shim.brier_score()`, never has to guess (or accidentally average
together) which kind of source produced a given confidence.

`answer_with_trace()` times one `Backend.answer()` call and appends a
single JSONL row to the same `MCP_TOOLCALL_LOG`-gated trace file
`record.py` writes MCP tool calls to, under `event: "jev/backend_answer"` —
one trace file, multiple event kinds, same `meta`/`debug` correlation-id
passthrough, rather than a second logging path.

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
