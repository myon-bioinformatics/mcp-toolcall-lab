"""OpenAI-compatible P1 backend for the Jev backend switch.

This adapter targets the repo's existing llama.cpp OpenAI-compatible server
without making it part of default CI. The HTTP transport is injected, so
plain pytest remains deterministic and network-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from mcp_toolcall_lab.jev_backend import BackendError, _validated
from mcp_toolcall_lab.jev_shim import KINDS

PROB_SOURCE_SELF_REPORTED = "self_reported"
PostFn = Callable[[str, dict[str, Any]], dict[str, Any]]


def _probability_schema() -> dict[str, Any]:
    return {"type": "number", "minimum": 0.0, "maximum": 1.0}


def answer_schema(question: dict[str, Any]) -> dict[str, Any]:
    """Return a strict JSON schema for the real TypeSafe answer wire."""
    kind = question.get("type")
    probability = _probability_schema()
    if kind == "noul":
        return {
            "type": "object",
            "properties": {"type": {"const": "noul"}, "noul": probability},
            "required": ["type", "noul"],
            "additionalProperties": False,
        }
    if kind == "choice":
        labels = list((question.get("criteria") or {}).keys())
        if not labels:
            raise BackendError("choice requires non-empty criteria")
        return {
            "type": "object",
            "properties": {
                "type": {"const": "choice"},
                "choice": {"type": "string", "enum": labels},
                "confidence": probability,
                "probabilities": {
                    "type": "object",
                    "properties": {label: probability for label in labels},
                    "required": labels,
                    "additionalProperties": False,
                },
            },
            "required": ["type", "choice", "confidence", "probabilities"],
            "additionalProperties": False,
        }
    if kind == "score":
        criteria = question.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise BackendError("score requires non-empty ordered criteria")
        keys = [str(index) for index in range(len(criteria))]
        return {
            "type": "object",
            "properties": {
                "type": {"const": "score"},
                "score": {"type": "number"},
                "confidence": probability,
                "legend": {
                    "type": "object",
                    "properties": {key: {"const": str(value)} for key, value in zip(keys, criteria)},
                    "required": keys,
                    "additionalProperties": False,
                },
                "probabilities": {
                    "type": "object",
                    "properties": {key: probability for key in keys},
                    "required": keys,
                    "additionalProperties": False,
                },
            },
            "required": ["type", "score", "confidence", "legend", "probabilities"],
            "additionalProperties": False,
        }
    raise BackendError(f"unsupported question kind {kind!r}; expected one of {KINDS}")


@dataclass
class OpenAICompatBackend:
    """Constrained generic-LLM adapter that emits the real Jev wire."""

    base_url: str
    model: str
    post: PostFn
    prob_source: str = PROB_SOURCE_SELF_REPORTED

    def answer(self, name: str, question: dict[str, Any], state: Any) -> dict[str, Any]:
        schema = answer_schema(question)
        body = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only the typed decision JSON required by the supplied JSON schema; do not explain.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"name": name, "state": state, "question": question},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": f"jev_{question['type']}", "strict": True, "schema": schema},
            },
        }
        response = self.post(f"{self.base_url.rstrip('/')}/v1/chat/completions", body)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError("OpenAI-compatible response is missing choices[0].message.content") from exc
        if not isinstance(content, str):
            raise BackendError("OpenAI-compatible message.content must be a JSON string")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BackendError("OpenAI-compatible completion is not valid JSON") from exc
        return _validated(name, payload)
