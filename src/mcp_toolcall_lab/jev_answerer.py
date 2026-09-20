"""Generate a live completion for jev_shim's three primitives (noul/score/choice).

jev_shim.py only judges *recorded* completions offline. This module is the
deferred "wire an actual completion source" follow-up named in
docs/jev_shim.md's non-goals -- it builds the OpenAI-compatible Chat
Completions request (system prompt + JSON-schema-constrained
``response_format``) for a given kind/state, and posts it to a configurable
HTTP endpoint.

**No live backend is called by this module's own tests or by CI.** The HTTP
POST function is injected (``post``), so tests supply a fake one that
returns a canned response -- proving the request-building and
response-extraction logic is correct without a running model server. This
repo has no llama.cpp binary or model weights available to actually
exercise end to end; ``HttpAnswerer`` is written against llama.cpp's own
documented ``/v1/chat/completions`` + ``response_format: json_schema``
contract (the same one OpenAI-compatible servers generally implement), but
that contract has **not been verified against a real llama.cpp process**
by this module or its tests. Treat ``HttpAnswerer`` as reviewed-by-reading
against a spec, not proven-by-running, until someone with a real server
confirms it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.request import Request, urlopen

from mcp_toolcall_lab.jev_shim import KIND_CHOICE, KIND_NOUL, KIND_SCORE, KINDS

_SYSTEM_PROMPT = (
    "You answer only in one of three schema-constrained shapes -- never free "
    "text, never an explanation outside the JSON object.\n"
    "- noul (yes/no proposition): {\"probability\": 0.0-1.0}\n"
    "- score (rate against a rubric): {\"score\": number, "
    "\"distribution\": {label: probability, ...summing to ~1.0}, "
    "\"confidence\": 0.0-1.0}\n"
    "- choice (classify among options): {\"choice\": one of the given options, "
    "\"distribution\": {option: probability, ...summing to ~1.0}, "
    "\"confidence\": 0.0-1.0}\n"
    "Return exactly the JSON object for the requested kind. No markdown "
    "fences, no prose before or after it."
)

_RESPONSE_SCHEMAS: dict[str, dict[str, Any]] = {
    KIND_NOUL: {
        "type": "object",
        "properties": {"probability": {"type": "number", "minimum": 0.0, "maximum": 1.0}},
        "required": ["probability"],
        "additionalProperties": False,
    },
    KIND_SCORE: {
        "type": "object",
        "properties": {
            "score": {"type": "number"},
            "distribution": {"type": "object"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["score", "distribution", "confidence"],
        "additionalProperties": False,
    },
    KIND_CHOICE: {
        "type": "object",
        "properties": {
            "choice": {"type": "string"},
            "distribution": {"type": "object"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["choice", "distribution", "confidence"],
        "additionalProperties": False,
    },
}


def _user_content(kind: str, state: str, *, proposition: str | None, criteria: str | None, options: list[str] | None) -> str:
    if kind == KIND_NOUL:
        return f"state:\n{state}\n\nproposition:\n{proposition}"
    if kind == KIND_SCORE:
        return f"state:\n{state}\n\ncriteria:\n{criteria}"
    if kind == KIND_CHOICE:
        return f"state:\n{state}\n\noptions:\n" + "\n".join(f"- {option}" for option in options or [])
    raise ValueError(f"unknown kind: {kind!r}")


def build_chat_completion_request(
    kind: str,
    state: str,
    *,
    model: str,
    proposition: str | None = None,
    criteria: str | None = None,
    options: list[str] | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Build an OpenAI-compatible Chat Completions request for one jev_shim kind.

    Uses ``response_format: {"type": "json_schema", ...}`` -- the same
    request shape llama.cpp's server and other OpenAI-compatible backends
    document for constrained decoding. Building this correctly does not by
    itself prove a given server honors it; see the module docstring.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    return {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _user_content(kind, state, proposition=proposition, criteria=criteria, options=options),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": f"jev_{kind}", "strict": True, "schema": _RESPONSE_SCHEMAS[kind]},
        },
    }


def extract_completion_text(response_body: dict[str, Any]) -> str:
    """Pull the raw completion string out of an OpenAI-compatible response body."""
    choices = response_body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("response has no choices[0]")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("response choices[0] has no message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("response message has no string content")
    return content


class PostFn(Protocol):
    def __call__(self, url: str, body: dict[str, Any]) -> dict[str, Any]: ...


def urllib_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Real HTTP POST via stdlib urllib. Never called by this module's tests."""
    data = json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:  # noqa: S310 -- explicit http(s) endpoint, caller-controlled
        return json.loads(response.read().decode("utf-8"))


@dataclass
class HttpAnswerer:
    """Builds a jev_shim request, posts it, and returns the raw completion text.

    ``post`` defaults to a real HTTP POST (``urllib_post``) but is always an
    injectable field precisely so tests never need a live server -- see the
    module docstring for what that does and doesn't prove.
    """

    base_url: str
    model: str
    post: Callable[[str, dict[str, Any]], dict[str, Any]] = urllib_post

    def raw_completion(
        self,
        kind: str,
        state: str,
        *,
        proposition: str | None = None,
        criteria: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        request_body = build_chat_completion_request(
            kind,
            state,
            model=self.model,
            proposition=proposition,
            criteria=criteria,
            options=options,
        )
        response_body = self.post(f"{self.base_url.rstrip('/')}/v1/chat/completions", request_body)
        return extract_completion_text(response_body)
