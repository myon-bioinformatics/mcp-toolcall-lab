"""jev_answerer: request-building and response-extraction only.

No live backend is called here -- ``post`` is always a fake. See the
module docstring in jev_answerer.py for exactly what that does and does
not prove about a real llama.cpp (or other OpenAI-compatible) server.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from mcp_toolcall_lab.jev_answerer import (
    HttpAnswerer,
    build_chat_completion_request,
    extract_completion_text,
    urllib_post,
)
from mcp_toolcall_lab.jev_shim import parse_completion, validate_payload


def _openai_response(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


# --- build_chat_completion_request -----------------------------------


def test_noul_request_shape() -> None:
    request = build_chat_completion_request(
        "noul", "some state", model="lab-model", proposition="the sky is blue"
    )
    assert request["model"] == "lab-model"
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][1]["role"] == "user"
    assert "the sky is blue" in request["messages"][1]["content"]
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["probability"]
    assert schema["additionalProperties"] is False


def test_score_request_shape() -> None:
    request = build_chat_completion_request("score", "state", model="m", criteria="rate 0-10")
    assert "rate 0-10" in request["messages"][1]["content"]
    schema = request["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {"score", "distribution", "confidence"}


def test_choice_request_shape_lists_options() -> None:
    request = build_chat_completion_request("choice", "state", model="m", options=["a", "b", "c"])
    content = request["messages"][1]["content"]
    assert "- a" in content and "- b" in content and "- c" in content
    schema = request["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {"choice", "distribution", "confidence"}


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        build_chat_completion_request("nonsense", "state", model="m")


# --- extract_completion_text --------------------------------------------


def test_extract_completion_text_happy_path() -> None:
    body = _openai_response('{"probability": 0.5}')
    assert extract_completion_text(body) == '{"probability": 0.5}'


def test_extract_completion_text_no_choices_raises() -> None:
    with pytest.raises(ValueError):
        extract_completion_text({"choices": []})


def test_extract_completion_text_missing_content_raises() -> None:
    with pytest.raises(ValueError):
        extract_completion_text({"choices": [{"message": {"role": "assistant"}}]})


# --- HttpAnswerer (fake post, no network) --------------------------------


def test_http_answerer_posts_to_v1_chat_completions_and_returns_content() -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        seen["url"] = url
        seen["body"] = body
        return _openai_response('{"probability": 0.8}')

    answerer = HttpAnswerer(base_url="http://127.0.0.1:8080", model="local-model", post=fake_post)
    text = answerer.raw_completion("noul", "a state", proposition="a proposition")

    assert seen["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert seen["body"]["model"] == "local-model"
    assert text == '{"probability": 0.8}'


def test_http_answerer_base_url_trailing_slash_is_handled() -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        seen["url"] = url
        return _openai_response("{}")

    answerer = HttpAnswerer(base_url="http://127.0.0.1:8080/", model="m", post=fake_post)
    answerer.raw_completion("choice", "state", options=["x"])
    assert seen["url"] == "http://127.0.0.1:8080/v1/chat/completions"


def test_http_answerer_output_composes_with_jev_shim_validation() -> None:
    """The point of the module: its output must be exactly what jev_shim expects."""

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return _openai_response('{"choice": "b", "distribution": {"a": 0.2, "b": 0.8}, "confidence": 0.7}')

    answerer = HttpAnswerer(base_url="http://localhost", model="m", post=fake_post)
    text = answerer.raw_completion("choice", "state", options=["a", "b"])

    payload = parse_completion(text)
    assert payload is not None
    assert validate_payload("choice", payload) is True


def test_http_answerer_malformed_model_output_is_still_extracted_but_fails_jev_shim_validation() -> None:
    """A live model ignoring response_format is exactly the failure jev_shim.md
    documents as the real, un-preventable-offline failure mode -- this proves
    that failure surfaces as a validation failure, not a crash in either module."""

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return _openai_response("I think it's probably true.")

    answerer = HttpAnswerer(base_url="http://localhost", model="m", post=fake_post)
    text = answerer.raw_completion("noul", "state", proposition="p")

    payload = parse_completion(text)
    assert payload is None


# --- urllib_post over a real loopback socket -----------------------------
#
# Every test above injects a fake `post`, so `urllib_post` -- the actual
# default HTTP implementation -- is otherwise never exercised. This spins up
# a real stdlib HTTP server on 127.0.0.1 (no external network) so the real
# request-encoding / response-decoding round trip is proven, not assumed.


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- stdlib signature
        pass  # keep test output quiet

    def do_POST(self) -> None:  # noqa: N802 -- stdlib method name
        length = int(self.headers.get("Content-Length", "0"))
        received = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.received_bodies.append(received)  # type: ignore[attr-defined]
        body = json.dumps(_openai_response('{"probability": 0.42}')).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_openai_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    server.received_bodies = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_urllib_post_round_trips_over_a_real_socket(fake_openai_server: HTTPServer) -> None:
    port = fake_openai_server.server_address[1]
    response = urllib_post(f"http://127.0.0.1:{port}/v1/chat/completions", {"model": "m", "messages": []})
    assert response["choices"][0]["message"]["content"] == '{"probability": 0.42}'
    assert fake_openai_server.received_bodies == [{"model": "m", "messages": []}]  # type: ignore[attr-defined]


def test_http_answerer_with_real_urllib_post_end_to_end(fake_openai_server: HTTPServer) -> None:
    """HttpAnswerer with its *default* post (urllib_post) against a real socket."""
    port = fake_openai_server.server_address[1]
    answerer = HttpAnswerer(base_url=f"http://127.0.0.1:{port}", model="m")
    text = answerer.raw_completion("noul", "state", proposition="p")
    assert text == '{"probability": 0.42}'
    payload = parse_completion(text)
    assert validate_payload("noul", payload) is True
