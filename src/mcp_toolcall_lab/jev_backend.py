"""jev_backend: minimal backend-switch + trace skeleton for Jev-assisted decisions.

Tracks issue #36 (backend switch design) / #37 (backend priority: P0
``fixture``, P0 ``typesafe_mock``, P1 ``openai_compat``, P2 Open Jev /
LocalJev, P3 TypeSafe live). This module is only the P0 slice -- the two
backends below -- plus the JSONL trace wiring both future backends and
future consumers (issue #39's ``jev_router``) will share. No
``openai_compat``/llama.cpp, Open Jev, or live TypeSafe backend here; those
are later increments, added behind the same ``Backend`` shape rather than
by changing it.

Both backends answer in TypeSafe's real ``noul``/``choice``/``score`` wire
shape -- the exact one ``jev_shim.validate_payload`` checks and
``jev_typesafe`` builds/parses -- so a downstream consumer never has to
know which backend answered:

- ``FixtureBackend``: replays a canned answer recorded in a JSON fixture
  file, keyed by question name. No HTTP client involved at all; fully
  deterministic and offline.
- ``TypeSafeMockBackend``: goes through the *real* request-building
  (``jev_typesafe.build_system_one_request``) and response-parsing
  (``jev_typesafe.parse_system_one_response``) code path via
  ``TypeSafeClient``, but with an injected in-process ``respond`` callable
  standing in for the real HTTP transport -- proves the same wire plumbing
  a live call would use, without a socket or ``TYPESAFE_API_KEY``.

Every answer is tagged with ``prob_source`` (``"fixture"`` or
``"typesafe_mock"``) -- #36/#37's field for telling a self-reported fixture
probability apart from one that actually flowed through the real
request/response shape, so ``jev_shim.brier_score()`` (or any future
confusion-matrix/calibration report) is never asked to average the two
together. A backend that composes another source (e.g. a future
``openai_compat`` backend wrapping a real completion) should set its own
``prob_source`` rather than reusing one of these two.

``answer_with_trace`` times one ``Backend.answer`` call and appends a
single JSONL row via the same ``MCP_TOOLCALL_LOG``-gated mechanism
``record.py`` uses for MCP tool calls, under a distinct
``event: "jev/backend_answer"`` -- one trace file, multiple event kinds,
same correlation ids (``meta``/``debug`` passthrough), rather than a
parallel logging path.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from mcp_toolcall_lab.jev_shim import KINDS, validate_payload
from mcp_toolcall_lab.jev_typesafe import TypeSafeClient
from mcp_toolcall_lab.mock.common import append_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "fixtures" / "jev_backend"

PROB_SOURCE_FIXTURE = "fixture"
PROB_SOURCE_TYPESAFE_MOCK = "typesafe_mock"

EVENT_BACKEND_ANSWER = "jev/backend_answer"


class BackendError(RuntimeError):
    """A backend could not produce a jev_shim-valid answer for a question."""


class Backend(Protocol):
    """Every backend answers a single named question in TypeSafe's real wire shape."""

    prob_source: str

    def answer(self, name: str, question: dict[str, Any], state: Any) -> dict[str, Any]: ...


def _validated(name: str, payload: Any) -> dict[str, Any]:
    kind = payload.get("type") if isinstance(payload, dict) else None
    if kind not in KINDS or not validate_payload(kind, payload):
        raise BackendError(f"answer for {name!r} is not a valid {kind!r} payload: {payload!r}")
    return payload


@dataclass
class FixtureBackend:
    """Deterministic, offline: replays a canned answer keyed by question name.

    ``fixtures`` maps question name -> a jev_shim-valid TypeSafe answer
    payload (e.g. ``{"type": "noul", "noul": 0.92}``). The actual
    ``question``/``state`` passed to ``answer`` are not consulted -- this
    backend only replays what was recorded, the same "fixture" role #37
    assigns it relative to ``typesafe_mock``/``openai_compat``/live.
    """

    fixtures: dict[str, dict[str, Any]]
    prob_source: str = PROB_SOURCE_FIXTURE

    @classmethod
    def from_path(cls, path: Path) -> "FixtureBackend":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path} is not a JSON object")
        return cls(fixtures=payload)

    def answer(self, name: str, question: dict[str, Any], state: Any) -> dict[str, Any]:
        del question, state
        if name not in self.fixtures:
            raise BackendError(f"no fixture recorded for question {name!r}")
        return _validated(name, self.fixtures[name])


RespondFn = Callable[[str, dict[str, Any], Any], dict[str, Any]]


@dataclass
class TypeSafeMockBackend:
    """Exercises the real /v1/systemone request+response shape without a socket.

    Wraps ``TypeSafeClient`` with an injected, deterministic ``respond``
    (question name, question, state) -> answer payload, standing in for the
    real HTTP transport (``TypeSafeClient.post``). This proves the same
    request-building/response-parsing path a live call would use, with zero
    external dependency and no API key -- the "typesafe_mock" backend #37
    ranks P0 alongside ``fixture``, distinct from a live TypeSafeClient
    call (P3) or an ``openai_compat`` completion (P1).
    """

    respond: RespondFn
    model: str = "jev-latest"
    prob_source: str = PROB_SOURCE_TYPESAFE_MOCK
    client: TypeSafeClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.client = TypeSafeClient(model=self.model, api_key="sk-mock", post=self._post)

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        del url, headers
        answers = {name: self.respond(name, question, body["state"]) for name, question in body["questions"].items()}
        return {"model": self.model, "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}

    def answer(self, name: str, question: dict[str, Any], state: Any) -> dict[str, Any]:
        answers = self.client.system_one(state, {name: question})
        return _validated(name, answers[name])


def record_backend_answer(
    *,
    backend: str,
    prob_source: str,
    name: str,
    kind: str,
    answer: dict[str, Any],
    duration_ms: float,
    meta: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> None:
    """Append one ``jev/backend_answer`` row when ``MCP_TOOLCALL_LOG`` is set.

    Same JSONL file and env-var gate as ``record.record_call``/
    ``record_protocol_event`` -- one trace file, distinguished by ``event``.
    """
    log_path = os.environ.get("MCP_TOOLCALL_LOG")
    if not log_path:
        return
    row: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(),
        "event": EVENT_BACKEND_ANSWER,
        "backend": backend,
        "prob_source": prob_source,
        "name": name,
        "kind": kind,
        "answer": answer,
        "duration_ms": duration_ms,
    }
    if meta:
        row["meta"] = meta
    if debug:
        row["debug"] = debug
        if debug.get("chat_id"):
            row["chat_id"] = debug["chat_id"]
    append_jsonl(log_path, row)


def answer_with_trace(
    backend: Backend,
    name: str,
    question: dict[str, Any],
    state: Any,
    *,
    meta: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call ``backend.answer``, time it, and append one JSONL trace row.

    Returns the answer payload unchanged; tracing is a side effect gated by
    ``MCP_TOOLCALL_LOG``, so calling this with the env var unset (the
    default `pytest -q` state) behaves exactly like calling
    ``backend.answer`` directly.
    """
    started = time.monotonic()
    payload = backend.answer(name, question, state)
    duration_ms = (time.monotonic() - started) * 1000
    record_backend_answer(
        backend=type(backend).__name__,
        prob_source=backend.prob_source,
        name=name,
        kind=str(payload.get("type", question.get("type", ""))),
        answer=payload,
        duration_ms=duration_ms,
        meta=meta,
        debug=debug,
    )
    return payload
