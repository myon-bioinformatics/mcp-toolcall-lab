"""Generate a live completion in a generic three-shape judgment vocabulary
(noul/score/choice) -- deliberately NOT TypeSafe's real wire format.

jev_shim.py originally guessed at a third-party "Jev" product's response
shape from a secondhand description and got the field names wrong; it has
since been corrected (see docs/jev_shim.md's changelog) to TypeSafe's real,
confirmed ``POST /v1/systemone`` contract, and ``jev_typesafe.py`` now
builds/parses that real wire format.

This module is a **separate, self-contained experiment**: can you *prompt*
an ordinary generative LLM into a similar noul/score/choice vocabulary (a
yes/no probability, a rubric score, a classification) and validate its
output shape? It intentionally keeps its own request prompt, its own
JSON-schema ``response_format``, and its own validators below -- none of
which are imported from or composed with ``jev_shim``'s. That decoupling
is deliberate, not an oversight: this module predates jev_shim's
correction and still targets the original, since-corrected guessed shape
(``{"probability": ...}``, ``distribution``, etc.); composing it with
jev_shim's real-TypeSafe validators would make this module's own tests
fail every time jev_shim's shape changes, coupling two unrelated
questions ("does a generic LLM follow a prompted shape" vs. "what does
TypeSafe's real API return") through a name collision alone.

It builds the OpenAI-compatible Chat Completions request (system prompt +
JSON-schema-constrained ``response_format``) for a given kind/state, and
posts it to a configurable HTTP endpoint.

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
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.request import Request, urlopen

KIND_NOUL = "noul"
KIND_SCORE = "score"
KIND_CHOICE = "choice"
KINDS = (KIND_NOUL, KIND_SCORE, KIND_CHOICE)

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
    """Build an OpenAI-compatible Chat Completions request for one of this module's kinds.

    Uses ``response_format: {"type": "json_schema", ...}`` -- the same
    request shape llama.cpp's server and other OpenAI-compatible backends
    document for constrained decoding. Building this correctly does not by
    itself prove a given server honors it; see the module docstring.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    if kind == KIND_NOUL and not proposition:
        raise ValueError("noul requires a non-empty proposition")
    if kind == KIND_SCORE and not criteria:
        raise ValueError("score requires non-empty criteria")
    if kind == KIND_CHOICE and not options:
        raise ValueError("choice requires at least one option")

    schema = _RESPONSE_SCHEMAS[kind]
    if kind == KIND_CHOICE:
        # Constrain the model's own choice to the actual candidate set, not
        # just "any string" -- an out-of-set label is wrong regardless of
        # how it's phrased, and the schema should say so.
        schema = {**schema, "properties": {**schema["properties"], "choice": {"type": "string", "enum": list(options)}}}

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
            "json_schema": {"name": f"jev_{kind}", "strict": True, "schema": schema},
        },
    }


def parse_completion(raw_completion: str) -> Any | None:
    """Parse a raw completion string. ``None`` on any non-JSON text.

    Self-contained (not ``jev_shim.parse_completion``) so this module never
    silently starts judging TypeSafe's real wire shape -- see the module
    docstring.
    """
    try:
        return json.loads(raw_completion)
    except (json.JSONDecodeError, TypeError):
        return None


def _is_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def _is_distribution(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    total = 0.0
    for probability in value.values():
        if not _is_probability(probability):
            return False
        total += float(probability)
    return math.isclose(total, 1.0, abs_tol=0.05)


def validate_noul_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"probability"}:
        return False
    return _is_probability(payload["probability"])


def validate_choice_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"choice", "distribution", "confidence"}:
        return False
    if not isinstance(payload["choice"], str) or not payload["choice"]:
        return False
    distribution = payload["distribution"]
    if not _is_distribution(distribution) or payload["choice"] not in distribution:
        return False
    return _is_probability(payload["confidence"])


def validate_score_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"score", "distribution", "confidence"}:
        return False
    if not isinstance(payload["score"], (int, float)) or isinstance(payload["score"], bool):
        return False
    if not _is_distribution(payload["distribution"]):
        return False
    return _is_probability(payload["confidence"])


_VALIDATORS = {
    KIND_NOUL: validate_noul_payload,
    KIND_SCORE: validate_score_payload,
    KIND_CHOICE: validate_choice_payload,
}


def validate_payload(kind: str, payload: Any) -> bool:
    """Validate a parsed payload against this module's own (generic-LLM) shape.

    Deliberately not ``jev_shim.validate_payload`` -- see the module docstring.
    """
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise ValueError(f"unknown kind: {kind!r}")
    return validator(payload)


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
    """Builds a chat-completion request in this module's own kind vocabulary, posts it, and returns the raw completion text.

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
