"""jev_typesafe: request/response modeling for TypeSafe's real /v1/systemone.

Fake-post tests prove request-building and response-parsing logic without a
live API key. test_urllib_post_against_real_loopback_server proves the
stdlib HTTP transport itself works, using a real socket -- a different
claim from proving TypeSafe's real server behaves as documented (see the
module docstring)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mcp_toolcall_lab.jev_shim import validate_payload
from mcp_toolcall_lab.jev_typesafe import (
    SYSTEM_ONE_PATH,
    TypeSafeClient,
    build_system_one_request,
    choice_question,
    noul_question,
    parse_system_one_response,
    score_question,
    urllib_post,
)


# --- question builders -----------------------------------------------------


def test_noul_question_minimal() -> None:
    assert noul_question() == {"type": "noul"}


def test_noul_question_with_criteria() -> None:
    question = noul_question(criteria={"true": "clearly yes", "false": "clearly no"})
    assert question == {"type": "noul", "criteria": {"true": "clearly yes", "false": "clearly no"}}


def test_choice_question_requires_options() -> None:
    with pytest.raises(ValueError):
        choice_question({})


def test_choice_question_shape() -> None:
    question = choice_question({"angry": "raised voice", "calm": None}, instructions="classify tone")
    assert question == {
        "type": "choice",
        "criteria": {"angry": "raised voice", "calm": None},
        "instructions": "classify tone",
    }


def test_score_question_requires_levels() -> None:
    with pytest.raises(ValueError):
        score_question([])


def test_score_question_shape() -> None:
    question = score_question(["no coverage", "basic", "thorough"])
    assert question == {"type": "score", "criteria": ["no coverage", "basic", "thorough"]}


# --- build_system_one_request -----------------------------------------------


def test_build_request_single_question() -> None:
    body = build_system_one_request("some state", {"is_ready": noul_question()}, model="jev-latest")
    assert body == {"state": "some state", "model": "jev-latest", "questions": {"is_ready": {"type": "noul"}}}


def test_build_request_multiple_questions_speculative_fan_out() -> None:
    questions = {
        "kind": choice_question({"click": None, "type_text": None}),
        "site": choice_question({"login_form": None, "search_results": None}),
    }
    body = build_system_one_request("screenshot state", questions)
    assert set(body["questions"]) == {"kind", "site"}
    assert body["model"] == "jev-latest"


def test_build_request_rejects_empty_questions() -> None:
    with pytest.raises(ValueError):
        build_system_one_request("state", {})


def test_build_request_rejects_unknown_question_type() -> None:
    with pytest.raises(ValueError):
        build_system_one_request("state", {"bad": {"type": "essay"}})


# --- parse_system_one_response ----------------------------------------------


def test_parse_response_extracts_answers() -> None:
    response = {
        "model": "jev-latest",
        "answers": {"tone": {"type": "choice", "choice": "angry", "confidence": 0.9, "probabilities": {"angry": 0.8, "calm": 0.1, "excited": 0.1}}},
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }
    answers = parse_system_one_response(response)
    assert answers == response["answers"]
    # Answers are already jev_shim-valid payloads, not a string needing re-parsing.
    assert validate_payload("choice", answers["tone"]) is True


def test_parse_response_rejects_missing_answers_key() -> None:
    with pytest.raises(ValueError):
        parse_system_one_response({"model": "jev-latest"})


# --- TypeSafeClient (fake post) ---------------------------------------------


def test_client_system_one_builds_request_and_headers() -> None:
    captured: dict = {}

    def fake_post(url: str, body: dict, headers: dict) -> dict:
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return {"model": "jev-latest", "answers": {"q": {"type": "noul", "noul": 0.5}}, "usage": {"input_tokens": 1, "output_tokens": 1}}

    client = TypeSafeClient(base_url="https://api.typesafe.ai", model="jev-latest", api_key="sk-test", post=fake_post)
    answers = client.system_one("state text", {"q": noul_question()})

    assert captured["url"] == f"https://api.typesafe.ai{SYSTEM_ONE_PATH}"
    assert captured["body"] == {"state": "state text", "model": "jev-latest", "questions": {"q": {"type": "noul"}}}
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert answers == {"q": {"type": "noul", "noul": 0.5}}


def test_client_without_api_key_omits_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    captured: dict = {}

    def fake_post(url: str, body: dict, headers: dict) -> dict:
        captured["headers"] = headers
        return {"model": "jev-latest", "answers": {}, "usage": {"input_tokens": 0, "output_tokens": 0}}

    client = TypeSafeClient(post=fake_post)
    client.system_one("state", {"q": noul_question()})
    assert "Authorization" not in captured["headers"]


def test_client_reads_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-from-env")
    captured: dict = {}

    def fake_post(url: str, body: dict, headers: dict) -> dict:
        captured["headers"] = headers
        return {"model": "jev-latest", "answers": {}, "usage": {"input_tokens": 0, "output_tokens": 0}}

    client = TypeSafeClient(post=fake_post)
    client.system_one("state", {"q": noul_question()})
    assert captured["headers"]["Authorization"] == "Bearer sk-from-env"


# --- real loopback transport (proves urllib_post itself works) -------------


class _CannedSystemOneHandler(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's naming
        length = int(self.headers.get("Content-Length", "0"))
        _CannedSystemOneHandler.received["path"] = self.path
        _CannedSystemOneHandler.received["headers"] = dict(self.headers)
        _CannedSystemOneHandler.received["body"] = json.loads(self.rfile.read(length))
        response = {
            "model": "jev-latest",
            "answers": {"is_ready": {"type": "noul", "noul": 0.81}},
            "usage": {"input_tokens": 42, "output_tokens": 3},
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- silence test output
        pass


def test_urllib_post_against_real_loopback_server() -> None:
    server = HTTPServer(("127.0.0.1", 0), _CannedSystemOneHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = TypeSafeClient(base_url=base_url, api_key="sk-loopback", post=urllib_post)
        answers = client.system_one("real socket state", {"is_ready": noul_question()})

        assert answers == {"is_ready": {"type": "noul", "noul": 0.81}}
        assert _CannedSystemOneHandler.received["path"] == SYSTEM_ONE_PATH
        assert _CannedSystemOneHandler.received["headers"]["Authorization"] == "Bearer sk-loopback"
        assert _CannedSystemOneHandler.received["body"] == {
            "state": "real socket state",
            "model": "jev-latest",
            "questions": {"is_ready": {"type": "noul"}},
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
