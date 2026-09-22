"""Backend switch skeleton for Jev-assisted decisions (#36/#37 minimum slice).

#39 wants a typed pre-LLM router (``needs_tool`` / ``tool_family`` /
``confidence``) that skips the LLM on high-confidence cases. Per #36/#37
(this repo's own prior art for that idea), such a router should sit on top
of a small backend-switch layer rather than being written directly against
one hardcoded transport, so the transport can move later --
``fixture`` -> ``typesafe_mock`` -> ``openai_compat`` (the existing
digest-pinned llama.cpp Docker) -> Open Jev / LocalJev -> real TypeSafe --
without rewriting router logic each time. This module is that switch, not
the router: a later, independent ``jev_router.py`` (its own module and
fixtures, per the #39 review) is meant to be a *consumer* of ``decide()``
below, not built into it.

Two backends land in this slice -- both P0 per #37, both offline and
deterministic:

- ``fixture`` (``FixtureBackend``): canned answers from a checked-in JSON
  file, keyed by question name. No model, no network -- the same
  "no live backend by default" posture as ``jev_shim.py``'s fixture replay.
- ``typesafe_mock`` (``TypeSafeMockBackend``): the real
  ``jev_typesafe.TypeSafeClient`` request-building/response-parsing code
  path, wired to an injected fake ``post`` instead of a live socket -- the
  same posture ``jev_typesafe.py``'s own tests already use. Not a call to
  the real ``api.typesafe.ai``.

Both backends answer in TypeSafe's real noul/choice/score wire shape
(checked with ``jev_shim.validate_payload``) so a consumer never has to
special-case which backend answered. ``prob_source`` on each decision
record is exactly the backend name that produced the number -- this keeps
self-reported confidence (a generic LLM stating its own probability, e.g. a
future ``jev_answerer``-backed source) and TypeSafe-shaped probabilities
from ever being pooled into the same calibration bucket by accident, which
was the #39 review's concern.

``decide()`` traces one row per question to ``MCP_TOOLCALL_LOG`` (same env
var, same ``append_jsonl``/``resolve_correlation`` correlation-id plumbing
``record.py`` already uses) as event ``jev/decision``, so one trace file can
eventually show decision -> (skip-or-call) -> tool result once a router
consumes this. No file is written and no network call happens unless a
caller sets that env var -- default ``pytest`` stays offline and
credential-free.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from mcp_toolcall_lab.jev_shim import validate_payload
from mcp_toolcall_lab.jev_typesafe import DEFAULT_MODEL, TypeSafeClient, build_system_one_request
from mcp_toolcall_lab.mock.common import append_jsonl
from mcp_toolcall_lab.record import resolve_correlation

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "fixtures" / "jev_backend"
DEFAULT_FIXTURE_FILE = DEFAULT_FIXTURE_DIR / "fixture_answers.json"

BACKEND_FIXTURE = "fixture"
BACKEND_TYPESAFE_MOCK = "typesafe_mock"

EVENT_JEV_DECISION = "jev/decision"


class Backend(Protocol):
    name: str

    def ask(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]: ...


@dataclass
class FixtureBackend:
    """Answers a fixed set of named questions from a canned, checked-in answer map."""

    answers_by_name: dict[str, dict[str, Any]]
    name: str = BACKEND_FIXTURE

    def ask(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        missing = [name for name in questions if name not in self.answers_by_name]
        if missing:
            raise KeyError(f"FixtureBackend has no canned answer(s) for: {missing}")
        return {name: self.answers_by_name[name] for name in questions}


def load_fixture_answers(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load ``{question_name: answer}`` from a checked-in JSON file.

    Every answer is validated with ``jev_shim.validate_payload`` at load
    time so a malformed fixture fails immediately, not on first use.
    """
    target = path or DEFAULT_FIXTURE_FILE
    answers = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(answers, dict):
        raise ValueError(f"{target} is not a JSON object")
    for name, answer in answers.items():
        kind = answer.get("type") if isinstance(answer, dict) else None
        if kind is None or not validate_payload(kind, answer):
            raise ValueError(f"{target}: answer {name!r} is not a valid noul/choice/score payload")
    return answers


def load_fixture_backend(path: Path | None = None) -> FixtureBackend:
    return FixtureBackend(answers_by_name=load_fixture_answers(path))


@dataclass
class TypeSafeMockBackend:
    """The real ``TypeSafeClient`` request/response code path over an injected fake ``post`` -- no live socket."""

    client: TypeSafeClient
    name: str = BACKEND_TYPESAFE_MOCK

    def ask(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return self.client.system_one(state, questions)


def make_typesafe_mock_backend(answers_by_name: dict[str, dict[str, Any]], *, model: str = DEFAULT_MODEL) -> TypeSafeMockBackend:
    """A ``TypeSafeMockBackend`` whose fake ``post`` answers any requested question name from ``answers_by_name``.

    Exercises the exact request-building/response-parsing path
    ``jev_typesafe`` would use against a live server, so this differs from
    ``FixtureBackend`` in *what's proven*, not in the wire shape produced.
    """

    def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        questions = body.get("questions", {})
        missing = [name for name in questions if name not in answers_by_name]
        if missing:
            raise KeyError(f"typesafe_mock backend has no canned answer(s) for: {missing}")
        return {
            "model": body.get("model", model),
            "answers": {name: answers_by_name[name] for name in questions},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    return TypeSafeMockBackend(client=TypeSafeClient(model=model, api_key=None, post=fake_post))


def decide(
    backend: Backend,
    state: Any,
    questions: dict[str, dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Ask ``backend`` for answers to ``questions``, validate the wire shape, and trace one row per question.

    Returns one record per question name with ``kind``, ``backend``,
    ``prob_source`` (== ``backend.name``), ``answer``, ``schema_valid``, and
    ``latency_ms``. Tracing only happens when ``MCP_TOOLCALL_LOG`` is set
    (the same gate ``record.record_call`` uses); default ``pytest`` never
    writes a file or touches a network socket for either shipped backend.
    """
    build_system_one_request(state, questions)  # same request-shape validation both backends get, regardless of transport
    started = time.monotonic()
    answers = backend.ask(state, questions)
    latency_ms = (time.monotonic() - started) * 1000

    records: list[dict[str, Any]] = []
    for name, question in questions.items():
        kind = question.get("type")
        answer = answers.get(name)
        schema_valid = answer is not None and kind is not None and validate_payload(kind, answer)
        records.append(
            {
                "name": name,
                "kind": kind,
                "backend": backend.name,
                "prob_source": backend.name,
                "answer": answer,
                "schema_valid": schema_valid,
                "latency_ms": latency_ms,
            }
        )
    _trace_decisions(records, meta=meta)
    return records


def _trace_decisions(records: list[dict[str, Any]], *, meta: dict[str, Any] | None) -> None:
    log_path = os.environ.get("MCP_TOOLCALL_LOG")
    if not log_path:
        return
    correlation = resolve_correlation(meta=meta)
    for record in records:
        row: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "event": EVENT_JEV_DECISION, **record}
        row["debug"] = correlation
        if correlation.get("chat_id"):
            row["chat_id"] = correlation["chat_id"]
        append_jsonl(log_path, row)
