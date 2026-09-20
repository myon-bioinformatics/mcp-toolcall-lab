"""Request/response modeling for TypeSafe's real ``POST /v1/systemone`` API.

jev_shim.py judges *recorded* completions against this wire shape offline.
jev_answerer.py (a separate, deliberately generic experiment) prompts an
arbitrary OpenAI-compatible chat backend into imitating a similar shape.
This module is neither of those: it builds the actual request body and
parses the actual response body of TypeSafe's own "System One" endpoint,
as confirmed by reading three real open-source clients and the official
``typesafe-sdk`` PyPI package's OpenAPI-generated schema (``docs.typesafe.ai``
itself is blocked by this sandbox's egress policy). See docs/jev_shim.md
for exactly what was read, where, and at which commit/version.

Confirmed contract:

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <TYPESAFE_API_KEY>
    Accept: application/json
    Content-Type: application/json

    {"state": ..., "model": "jev-latest",
     "questions": {"<name>": {"type": "noul"|"choice"|"score", ...}}}

    -> {"model": "jev-latest",
        "answers": {"<name>": {"type": ..., ...}},
        "usage": {"input_tokens": int, "output_tokens": int}}

Multiple named questions can (and, per all three real clients, typically
do) travel in a single request -- a "speculative fan-out" rather than one
question per round trip.

Answers come back as the exact shapes ``jev_shim.validate_payload``
already checks (native JSON fields, not a chat-completion string to
re-parse) -- this module builds the request and unwraps the envelope;
``jev_shim`` still owns what counts as a valid answer.

**No live TypeSafe API key exists in this sandbox and no real call has
been made.** ``TypeSafeClient``'s ``post`` is injectable specifically so
tests can prove the request-building and response-parsing logic without
one; a real ``http.server`` loopback test proves the stdlib HTTP transport
itself works, which is a different claim from proving TypeSafe's real
server behaves as documented here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.request import Request, urlopen

from mcp_toolcall_lab.jev_shim import KIND_CHOICE, KIND_NOUL, KIND_SCORE, KINDS

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 10.0

SYSTEM_ONE_PATH = "/v1/systemone"


def noul_question(*, instructions: Any | None = None, criteria: dict[str, Any] | None = None) -> dict[str, Any]:
    """A yes/no proposition. ``criteria`` optionally maps ``"true"``/``"false"`` to guidance."""
    question: dict[str, Any] = {"type": KIND_NOUL}
    if instructions is not None:
        question["instructions"] = instructions
    if criteria is not None:
        question["criteria"] = criteria
    return question


def choice_question(options: dict[str, str | None], *, instructions: Any | None = None) -> dict[str, Any]:
    """A classification among labeled options. ``options`` maps label -> description (or ``None``)."""
    if not options:
        raise ValueError("choice_question requires at least one option")
    question: dict[str, Any] = {"type": KIND_CHOICE, "criteria": dict(options)}
    if instructions is not None:
        question["instructions"] = instructions
    return question


def score_question(levels: list[str], *, instructions: Any | None = None) -> dict[str, Any]:
    """An ordered rubric. ``levels`` is the ordered list of descriptions (position 0 first)."""
    if not levels:
        raise ValueError("score_question requires at least one level")
    question: dict[str, Any] = {"type": KIND_SCORE, "criteria": list(levels)}
    if instructions is not None:
        question["instructions"] = instructions
    return question


def build_system_one_request(state: Any, questions: dict[str, dict[str, Any]], *, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Build a ``/v1/systemone`` request body for one or more named questions."""
    if not questions:
        raise ValueError("build_system_one_request requires at least one question")
    for name, question in questions.items():
        if question.get("type") not in KINDS:
            raise ValueError(f"question {name!r}: type must be one of {KINDS}, got {question.get('type')!r}")
    return {"state": state, "model": model, "questions": questions}


def parse_system_one_response(response_body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Unwrap a ``/v1/systemone`` response body to its ``{name: answer}`` map.

    Does not itself validate individual answer shapes -- pass each answer
    to ``jev_shim.validate_payload(answer["type"], answer)`` for that.
    """
    answers = response_body.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("response has no answers object")
    return answers


class PostFn(Protocol):
    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]: ...


def urllib_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Real HTTP POST via stdlib urllib. Never called by this module's fake-post tests."""
    data = json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers=headers, method="POST")
    with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:  # noqa: S310 -- explicit http(s) endpoint, caller-controlled
        return json.loads(response.read().decode("utf-8"))


@dataclass
class TypeSafeClient:
    """Builds a ``/v1/systemone`` request, posts it, and returns the parsed answers.

    ``post`` defaults to a real HTTP POST (``urllib_post``) but is always
    injectable so tests never need a live API key -- see the module
    docstring for what that does and doesn't prove. ``api_key`` defaults to
    the ``TYPESAFE_API_KEY`` environment variable, matching the real SDK.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = field(default_factory=lambda: os.environ.get("TYPESAFE_API_KEY"))
    post: Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]] = urllib_post

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def system_one(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        request_body = build_system_one_request(state, questions, model=self.model)
        response_body = self.post(f"{self.base_url.rstrip('/')}{SYSTEM_ONE_PATH}", request_body, self._headers())
        return parse_system_one_response(response_body)
