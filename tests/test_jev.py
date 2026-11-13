"""Jev backends, answerer, router, shim and benchmarks: offline fixtures, no sockets or API keys."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab.ironmate_client import IronmateClient
from mcp_toolcall_lab.jev_answerer import (
    HttpAnswerer, build_chat_completion_request, extract_completion_text, parse_completion, urllib_post, validate_payload,
)
from mcp_toolcall_lab.jev_assisted import benchmark_row, benchmark_summary, execute_jev_assisted
from mcp_toolcall_lab.jev_backend import (
    BackendError, DEFAULT_FIXTURE_DIR, FixtureBackend, KeywordBackend, PROB_SOURCE_FIXTURE, PROB_SOURCE_KEYWORD, PROB_SOURCE_TYPESAFE_MOCK,
    TypeSafeMockBackend, answer_with_trace,
)
from mcp_toolcall_lab.jev_benchmark import load_cases, run_offline_comparison
from mcp_toolcall_lab.jev_live_benchmark import run_backend_routing_benchmark, urllib_json_post
from mcp_toolcall_lab.jev_openai_compat import OpenAICompatBackend, PROB_SOURCE_SELF_REPORTED, answer_schema
from mcp_toolcall_lab.jev_router import calibration, confusion_matrix, decide_route, latency_summary
from mcp_toolcall_lab.jev_shim import (
    DEFAULT_FIXTURE_DIR as DEFAULT_FIXTURE_DIR_jev_shim, VERDICT_PASS, brier_score, calibration_summary, iter_cases, main,
    parse_completion as parse_completion_jev_shim, replay_case, replay_fixtures, validate_choice_payload, validate_noul_payload,
    validate_payload as validate_payload_jev_backend, validate_score_payload,
)
from mcp_toolcall_lab.jev_typesafe import (
    SYSTEM_ONE_PATH, TypeSafeClient, build_system_one_request, choice_question, noul_question, parse_system_one_response, score_question,
    urllib_post as urllib_post_jev_typesafe,
)


# ═══ · jev answerer ══════════════════════════════════════════════════════════════════════════════════════
# jev_answerer: request-building and response-extraction only.
#
# No live backend is called here -- ``post`` is always a fake. See the
# module docstring in jev_answerer.py for exactly what that does and does
# not prove about a real llama.cpp (or other OpenAI-compatible) server, and
# why this module's ``parse_completion``/``validate_payload`` are its own
# and deliberately not ``jev_shim``'s.

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
    # The candidate set itself is constrained, not just "any string".
    assert schema["properties"]["choice"]["enum"] == ["a", "b", "c"]


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        build_chat_completion_request("nonsense", "state", model="m")


def test_noul_without_proposition_raises() -> None:
    with pytest.raises(ValueError):
        build_chat_completion_request("noul", "state", model="m")


def test_score_without_criteria_raises() -> None:
    with pytest.raises(ValueError):
        build_chat_completion_request("score", "state", model="m")


def test_choice_without_options_raises() -> None:
    with pytest.raises(ValueError):
        build_chat_completion_request("choice", "state", model="m")
    with pytest.raises(ValueError):
        build_chat_completion_request("choice", "state", model="m", options=[])


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


def test_http_answerer_output_validates_against_own_shape() -> None:
    """The point of the module: its output must match its own declared shape
    (deliberately not jev_shim's -- see the module docstring)."""

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return _openai_response('{"choice": "b", "distribution": {"a": 0.2, "b": 0.8}, "confidence": 0.7}')

    answerer = HttpAnswerer(base_url="http://localhost", model="m", post=fake_post)
    text = answerer.raw_completion("choice", "state", options=["a", "b"])

    payload = parse_completion(text)
    assert payload is not None
    assert validate_payload("choice", payload) is True


def test_http_answerer_malformed_model_output_is_still_extracted_but_fails_validation() -> None:
    """A live model ignoring response_format is exactly the failure jev-shim.md
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


# ═══ · jev assisted ══════════════════════════════════════════════════════════════════════════════════════

class FakeSession:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return {"result": {"tools": []}}

    def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if self.error is not None:
            raise self.error
        return {"result": {"ok": True}}


def backend(needs: float, family: str = "ironmate", confidence: float = .95):
    fixtures = {"needs_tool": {"type": "noul", "noul": needs}}
    if needs >= .5:
        remainder = (1.0 - confidence) / 3
        fixtures["tool_family"] = {
            "type": "choice",
            "choice": family,
            "confidence": confidence,
            "probabilities": {
                name: confidence if name == family else remainder
                for name in ("ironmate", "wikipedia", "mock", "none")
            },
        }
    return FixtureBackend(fixtures=fixtures)


def test_high_confidence_ironmate_skips_llm_and_calls_mcp():
    session = FakeSession()
    fallback_calls = []
    result = execute_jev_assisted(
        backend(.98),
        "find repository metadata",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "markdown"},
        fallback=lambda state: fallback_calls.append(state),
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert result.execution_fallback_reason is None
    assert result.result == {"result": {"ok": True}}
    assert fallback_calls == []
    assert session.calls[0][0] == "search_repository_metadata"
    assert session.calls[0][2]["meta"]["source"] == "jev-router"


def test_ironmate_call_error_degrades_to_existing_fallback():
    session = FakeSession(error=RuntimeError("transport down"))
    result = execute_jev_assisted(
        backend(.98),
        "find repository metadata",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "markdown"},
        fallback=lambda state: {"llm": state},
    )
    assert result.llm_used is True
    assert result.tool_called is True
    assert result.execution_fallback_reason == "ironmate_call_error"
    assert result.result == {"llm": "find repository metadata"}
    assert len(session.calls) == 1


def test_low_confidence_ironmate_preserves_existing_fallback():
    session = FakeSession()
    result = execute_jev_assisted(
        backend(.6, confidence=.6),
        "ambiguous",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "x"},
        fallback=lambda state: {"llm": state},
    )
    assert result.llm_used is True
    assert result.tool_called is False
    assert result.result == {"llm": "ambiguous"}
    assert result.decision.fallback_reason == "low_confidence"
    assert session.calls == []


def test_non_ironmate_family_stays_on_existing_flow_in_first_slice():
    result = execute_jev_assisted(
        backend(.98, family="wikipedia"),
        "Wikipedia please",
        ironmate_client=IronmateClient(FakeSession()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
    )
    assert result.llm_used is True
    assert result.result == "existing-flow"


def test_low_confidence_no_tool_preserves_existing_fallback():
    session = FakeSession()
    result = execute_jev_assisted(
        backend(.4),
        "maybe no tool",
        ironmate_client=IronmateClient(session),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: {"llm": state},
    )
    assert result.llm_used is True
    assert result.tool_called is False
    assert result.decision.fallback_reason == "low_confidence"
    assert result.result == {"llm": "maybe no tool"}
    assert session.calls == []


def test_high_confidence_no_tool_avoids_llm_without_calling_tool():
    session = FakeSession()
    result = execute_jev_assisted(
        backend(.02),
        "hello",
        ironmate_client=IronmateClient(session),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "should-not-run",
    )
    assert result.llm_used is False
    assert result.tool_called is False
    assert result.result is None
    assert session.calls == []


def test_benchmark_surface_counts_avoided_wrong_fallback_and_success():
    session = FakeSession()
    direct = execute_jev_assisted(
        backend(.98),
        "metadata",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "markdown"},
        fallback=lambda state: "fallback",
    )
    fallback = execute_jev_assisted(
        backend(.6, confidence=.6),
        "ambiguous",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "x"},
        fallback=lambda state: "fallback",
    )
    rows = [
        benchmark_row(direct, expected_tool_family="ironmate", tool_success=True),
        benchmark_row(fallback, expected_tool_family="ironmate"),
    ]
    summary = benchmark_summary(rows)
    assert summary["cases"] == 2
    assert summary["llm_calls_avoided"] == 1
    assert summary["wrong_routes"] == 0
    assert summary["policy_fallbacks"] == 1
    assert summary["execution_fallbacks"] == 0
    assert summary["out_of_scope_family"] == 0
    assert summary["tool_successes"] == 1
    assert summary["tool_attempts"] == 1


def test_benchmark_separates_policy_fallback_from_out_of_scope_family():
    session = FakeSession()
    wikipedia = execute_jev_assisted(
        backend(.98, family="wikipedia"),
        "Wikipedia please",
        ironmate_client=IronmateClient(session),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
    )
    policy = execute_jev_assisted(
        backend(.6, confidence=.6),
        "ambiguous",
        ironmate_client=IronmateClient(session),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
    )
    summary = benchmark_summary([
        benchmark_row(wikipedia, expected_tool_family="wikipedia"),
        benchmark_row(policy, expected_tool_family="ironmate"),
    ])
    assert summary["policy_fallbacks"] == 1
    assert summary["out_of_scope_family"] == 1
    assert summary["wrong_routes"] == 0


def test_benchmark_separates_execution_failure_from_scope_fallback():
    failed = execute_jev_assisted(
        backend(.98),
        "metadata",
        ironmate_client=IronmateClient(FakeSession(error=RuntimeError("down"))),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "x"},
        fallback=lambda state: "existing-flow",
    )
    summary = benchmark_summary([
        benchmark_row(failed, expected_tool_family="ironmate", tool_success=False),
    ])
    assert summary["policy_fallbacks"] == 0
    assert summary["execution_fallbacks"] == 1
    assert summary["out_of_scope_family"] == 0
    assert summary["tool_attempts"] == 1
    assert summary["tool_successes"] == 0


# ═══ · jev backend ═══════════════════════════════════════════════════════════════════════════════════════
# jev_backend: fixture + typesafe_mock backend-switch skeleton (#36/#37).
#
# No sockets, no TYPESAFE_API_KEY -- both backends are offline/deterministic,
# matching this repo's "default pytest stays network-free" convention.

def _sample_fixture_backend() -> FixtureBackend:
    return FixtureBackend.from_path(DEFAULT_FIXTURE_DIR / "sample_answers.json")


# --- FixtureBackend ----------------------------------------------------


def test_default_fixture_dir_is_under_fixtures_jev_backend() -> None:
    assert DEFAULT_FIXTURE_DIR.name == "jev_backend"
    assert DEFAULT_FIXTURE_DIR.parent.name == "fixtures"


def test_fixture_backend_replays_recorded_answer() -> None:
    backend = _sample_fixture_backend()
    payload = backend.answer("is_actionable", noul_question(), state="a state string")
    assert payload == {"type": "noul", "noul": 0.88}
    assert backend.prob_source == PROB_SOURCE_FIXTURE


def test_fixture_backend_ignores_question_and_state() -> None:
    """Fixture replay is keyed by name only -- it does not consult question/state."""
    backend = _sample_fixture_backend()
    a = backend.answer("category", {"type": "choice", "criteria": {"x": None}}, state="state A")
    b = backend.answer("category", {"type": "choice", "criteria": {"y": None}}, state="state B")
    assert a == b


def test_fixture_backend_missing_question_raises() -> None:
    backend = _sample_fixture_backend()
    with pytest.raises(BackendError, match="no fixture recorded"):
        backend.answer("unknown_question", noul_question(), state="x")


def test_fixture_backend_rejects_invalid_recorded_payload() -> None:
    backend = FixtureBackend(fixtures={"bad": {"type": "noul", "noul": 1.5}})
    with pytest.raises(BackendError, match="not a valid"):
        backend.answer("bad", noul_question(), state="x")


def test_fixture_backend_from_path_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "not_an_object.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        FixtureBackend.from_path(path)


def test_shipped_sample_fixtures_are_all_wire_valid() -> None:
    payload = json.loads((DEFAULT_FIXTURE_DIR / "sample_answers.json").read_text(encoding="utf-8"))
    assert set(payload) == {"is_actionable", "category", "severity"}
    for answer in payload.values():
        assert validate_payload_jev_backend(answer["type"], answer) is True


# --- TypeSafeMockBackend -------------------------------------------------


def test_typesafe_mock_backend_round_trips_through_real_wire_shapes() -> None:
    captured: dict = {}

    def respond(name: str, question: dict, state) -> dict:
        captured["name"] = name
        captured["question"] = question
        captured["state"] = state
        return {"type": "noul", "noul": 0.42}

    backend = TypeSafeMockBackend(respond=respond)
    payload = backend.answer("is_ready", noul_question(criteria={"true": "yes"}), state="some state")

    assert payload == {"type": "noul", "noul": 0.42}
    assert backend.prob_source == PROB_SOURCE_TYPESAFE_MOCK
    assert captured["name"] == "is_ready"
    assert captured["question"] == {"type": "noul", "criteria": {"true": "yes"}}
    assert captured["state"] == "some state"


def test_typesafe_mock_backend_never_calls_real_transport() -> None:
    """respond stands in for TypeSafeClient.post -- urllib_post is never reached."""

    def respond(name: str, question: dict, state) -> dict:
        return {"type": "choice", "choice": "a", "confidence": 0.5, "probabilities": {"a": 0.5, "b": 0.5}}

    backend = TypeSafeMockBackend(respond=respond)
    assert backend.client.post is not None
    assert backend.client.post.__func__ is TypeSafeMockBackend._post  # bound to this instance, not urllib_post
    payload = backend.answer("q", {"type": "choice", "criteria": {"a": None, "b": None}}, state="s")
    assert payload["choice"] == "a"


def test_typesafe_mock_backend_rejects_invalid_response_shape() -> None:
    def respond(name: str, question: dict, state) -> dict:
        return {"type": "noul", "noul": "not a number"}

    backend = TypeSafeMockBackend(respond=respond)
    with pytest.raises(BackendError, match="not a valid"):
        backend.answer("q", noul_question(), state="s")


# --- answer_with_trace ----------------------------------------------------


def test_answer_with_trace_returns_payload_unchanged_without_log_env(monkeypatch) -> None:
    monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)
    backend = _sample_fixture_backend()
    payload = answer_with_trace(backend, "is_actionable", noul_question(), state="s")
    assert payload == {"type": "noul", "noul": 0.88}


def test_answer_with_trace_appends_jsonl_row_when_log_set(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    backend = _sample_fixture_backend()
    answer_with_trace(
        backend,
        "is_actionable",
        noul_question(),
        state="s",
        meta={"chat_id": "chat_wire"},
        debug={"chat_id": "chat_wire", "chat_id_source": "meta"},
    )
    monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "jev/backend_answer"
    assert row["backend"] == "FixtureBackend"
    assert row["prob_source"] == "fixture"
    assert row["name"] == "is_actionable"
    assert row["kind"] == "noul"
    assert row["answer"] == {"type": "noul", "noul": 0.88}
    assert isinstance(row["duration_ms"], float)
    assert row["duration_ms"] >= 0.0
    assert row["meta"] == {"chat_id": "chat_wire"}
    assert row["chat_id"] == "chat_wire"
    assert row["debug"]["chat_id_source"] == "meta"


def test_answer_with_trace_tags_typesafe_mock_prob_source(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))

    def respond(name: str, question: dict, state) -> dict:
        return {"type": "noul", "noul": 0.9}

    backend = TypeSafeMockBackend(respond=respond)
    answer_with_trace(backend, "is_ready", noul_question(), state="s")
    monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)

    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["prob_source"] == "typesafe_mock"
    assert row["backend"] == "TypeSafeMockBackend"


# --- KeywordBackend (light local judgment) -----------------------------

BENCHMARK = Path(__file__).parents[1] / "fixtures" / "jev_assisted" / "benchmark_cases.json"


def _route(text):
    from mcp_toolcall_lab.jev_router import decide_route

    return decide_route(KeywordBackend.from_path(), text)


def test_keyword_backend_never_routes_wrong_on_the_benchmark_cases():
    # The weights were written while looking at these 12 cases, so this is a
    # regression guard for the policy, not held-out evidence of accuracy.
    from dataclasses import asdict

    from mcp_toolcall_lab.jev_router import confusion_matrix, decide_route

    backend = KeywordBackend.from_path()
    rows = []
    for case in json.loads(BENCHMARK.read_text(encoding="utf-8")):
        rows.append({**asdict(decide_route(backend, case["state"])), "expected_tool_family": case["expected_tool_family"]})
    matrix = confusion_matrix(rows)
    assert matrix["routed-wrong"] == 0 and matrix["fallback-wrong"] == 0
    assert {row["prob_source"] for row in rows} == {PROB_SOURCE_KEYWORD}


@pytest.mark.parametrize(
    "text",
    ["ポケモンのピカチュウの種族値を調べて", "What are Pikachu's base stats in the Pokémon API?", "PokeAPIでditto取って"],
)
def test_keyword_backend_routes_pokemon_requests_to_pokeapi(text):
    decision = _route(text)
    assert (decision.tool_family, decision.fallback_reason) == ("pokeapi", None)


@pytest.mark.parametrize(
    ("text", "family", "reason"),
    [
        ("Wikipediaでポケモンの記事を見て", "wikipedia", "low_confidence"),  # two families pull equally
        ("Look this up if necessary", None, "low_confidence"),  # vague "find it": hand to the LLM
        ("この文章を短く書き直して。", "none", None),
    ],
)
def test_keyword_backend_falls_back_when_unsure_and_says_none_when_no_cue(text, family, reason):
    decision = _route(text)
    assert decision.fallback_reason == reason
    if family is not None:
        assert decision.tool_family == family


def test_keyword_backend_answers_are_valid_payloads_and_reject_unknown_questions():
    backend = KeywordBackend(weights={"pokeapi": {"pokemon": 3}})
    noul = backend.answer("needs_tool", {}, "pokemon")
    assert validate_payload_jev_backend("noul", noul) and noul["noul"] == pytest.approx(0.875)
    choice = backend.answer("tool_family", {"criteria": {"pokeapi": None, "none": None}}, "pokemon")
    assert validate_payload_jev_backend("choice", choice) and choice["choice"] == "pokeapi"
    with pytest.raises(BackendError):
        backend.answer("something_else", {}, "pokemon")


def test_keyword_judgment_then_keyword_fetch_end_to_end_offline():
    # judge -> caller-supplied keyword -> pokeapi/source (pasted view-source JSON) -> Markdown
    from mcp_toolcall_lab.jev_assisted import execute_jev_assisted
    from mcp_toolcall_lab.source_access import default_registry

    source = (Path(__file__).parents[1] / "fixtures" / "pokeapi" / "ditto.json").read_text(encoding="utf-8")
    result = execute_jev_assisted(
        KeywordBackend.from_path(),
        "ポケモンのditto、種族値を教えて",
        ironmate_client=None,
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "LLM",
        direct_calls={"pokeapi": lambda: default_registry().invoke("pokeapi", "source", {"source": source})},
    )
    assert (result.decision.tool_family, result.tool_called, result.llm_used) == ("pokeapi", True, False)
    assert result.result["data"]["name"] == "ditto"


# ═══ · jev benchmark ═════════════════════════════════════════════════════════════════════════════════════

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jev_assisted" / "benchmark_cases.json"


class FakeSession_jev_benchmark:
    def __init__(self):
        self.calls = []

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return {"result": {"tools": []}}

    def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        return {"result": {"ok": True}}


def test_offline_comparison_has_mixed_realistic_cases():
    cases = load_cases(FIXTURE)
    assert len(cases) == 12
    assert {case["expected_tool_family"] for case in cases} == {
        "ironmate", "wikipedia", "mock", "none"
    }
    assert any("Ironmate" in case["state"] for case in cases)
    assert any("Wikipedia" in case["state"] for case in cases)
    assert any(any(ord(ch) > 127 for ch in case["state"]) for case in cases)


def test_offline_comparison_quantifies_baseline_vs_assisted_policy():
    session = FakeSession_jev_benchmark()
    fallback_calls = []
    report = run_offline_comparison(
        load_cases(FIXTURE),
        ironmate_client=IronmateClient(session),
        fallback=lambda state: fallback_calls.append(state) or {"fallback": state},
    )

    assert report["prob_sources"] == ["fixture"]
    assert report["baseline"]["llm_calls"] == 12
    assert report["assisted"]["llm_calls"] == 6
    assert report["delta"]["llm_calls"] == -6
    assert report["delta"]["llm_calls_avoided"] == 6
    assert report["delta"]["correctness_regressions"] == 0

    assert report["assisted"]["policy_fallbacks"] == 1
    assert report["assisted"]["execution_fallbacks"] == 0
    assert report["assisted"]["out_of_scope_family"] == 5
    assert report["assisted"]["tool_attempts"] == 3
    assert report["assisted"]["tool_successes"] == 3
    assert len(session.calls) == 3
    assert len(fallback_calls) == 6


def test_report_rows_keep_case_ids_and_expected_families():
    report = run_offline_comparison(
        load_cases(FIXTURE),
        ironmate_client=IronmateClient(FakeSession_jev_benchmark()),
        fallback=lambda state: state,
    )
    rows = report["rows"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["expected_tool_family"] for row in rows} == {
        "ironmate", "wikipedia", "mock", "none"
    }


# ═══ · jev family direct ═════════════════════════════════════════════════════════════════════════════════

class FakeSession_jev_family_direct:
    def call_tool(self, name, arguments, **kwargs):
        return {"result": {"ok": True}}


def backend_jev_family_direct(family: str):
    return FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.98},
        "tool_family": {
            "type": "choice",
            "choice": family,
            "confidence": 0.96,
            "probabilities": {
                name: 0.96 if name == family else (0.04 / 3)
                for name in ("ironmate", "wikipedia", "mock", "none")
            },
        },
    })


def test_high_confidence_wikipedia_can_skip_selector_llm():
    calls = []
    result = execute_jev_assisted(
        backend_jev_family_direct("wikipedia"),
        "Wikipedia BLEACH",
        ironmate_client=IronmateClient(FakeSession_jev_family_direct()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "fallback",
        direct_calls={"wikipedia": lambda: calls.append("wiki") or {"title": "BLEACH"}},
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert result.result == {"title": "BLEACH"}
    assert calls == ["wiki"]


def test_high_confidence_mock_can_skip_selector_llm():
    result = execute_jev_assisted(
        backend_jev_family_direct("mock"),
        "exercise mock",
        ironmate_client=IronmateClient(FakeSession_jev_family_direct()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "fallback",
        direct_calls={"mock": lambda: [{"source": "mock"}]},
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert result.result == [{"source": "mock"}]


def test_unwired_family_still_uses_existing_flow():
    result = execute_jev_assisted(
        backend_jev_family_direct("wikipedia"),
        "Wikipedia",
        ironmate_client=IronmateClient(FakeSession_jev_family_direct()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
    )
    assert result.llm_used is True
    assert result.tool_called is False
    assert result.result == "existing-flow"


def test_direct_family_failure_degrades_to_existing_flow():
    def fail():
        raise RuntimeError("down")

    result = execute_jev_assisted(
        backend_jev_family_direct("wikipedia"),
        "Wikipedia",
        ironmate_client=IronmateClient(FakeSession_jev_family_direct()),
        ironmate_tool="unused",
        ironmate_arguments={},
        fallback=lambda state: "existing-flow",
        direct_calls={"wikipedia": fail},
    )
    assert result.llm_used is True
    assert result.tool_called is True
    assert result.result == "existing-flow"
    assert result.execution_fallback_reason == "wikipedia_call_error"


def test_ironmate_direct_call_cannot_bypass_dedicated_client_boundary():
    calls = []
    session = FakeSession_jev_family_direct()
    result = execute_jev_assisted(
        backend_jev_family_direct("ironmate"),
        "ironmate",
        ironmate_client=IronmateClient(session),
        ironmate_tool="search_repository_metadata",
        ironmate_arguments={"query": "mcp"},
        fallback=lambda state: "existing-flow",
        direct_calls={"ironmate": lambda: calls.append("bad")},
    )
    assert result.llm_used is False
    assert result.tool_called is True
    assert calls == []
    assert result.result == {"result": {"ok": True}}


# ═══ · jev live benchmark ════════════════════════════════════════════════════════════════════════════════

def backend_jev_live_benchmark():
    return FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.98},
        "tool_family": {
            "type": "choice",
            "choice": "ironmate",
            "confidence": 0.95,
            "probabilities": {
                "ironmate": 0.95,
                "wikipedia": 0.02,
                "mock": 0.02,
                "none": 0.01,
            },
        },
    })


def test_measured_backend_report_keeps_provenance_and_correctness():
    report = run_backend_routing_benchmark([
        {"id": "a", "state": "repository metadata", "expected_tool_family": "ironmate"},
        {"id": "b", "state": "also metadata", "expected_tool_family": "wikipedia"},
    ], backend_jev_live_benchmark())

    assert report["mode"] == "measured-backend-routing"
    assert report["prob_sources"] == ["fixture"]
    assert report["cases"] == 2
    assert report["routed"] == 2
    assert report["fallbacks"] == 0
    assert report["wrong_routes"] == 1
    assert report["selector_calls_skipped_by_policy"] == 2
    assert report["confusion_matrix"]["routed-correct"] == 1
    assert report["confusion_matrix"]["routed-wrong"] == 1
    assert report["calibration"]["fixture"]["n"] == 2


def test_low_confidence_is_fallback_not_avoided():
    low = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.6},
        "tool_family": {
            "type": "choice",
            "choice": "ironmate",
            "confidence": 0.6,
            "probabilities": {
                "ironmate": 0.6,
                "wikipedia": 0.14,
                "mock": 0.13,
                "none": 0.13,
            },
        },
    })
    report = run_backend_routing_benchmark([
        {"id": "a", "state": "ambiguous", "expected_tool_family": "ironmate"},
    ], low)
    assert report["fallbacks"] == 1
    assert report["selector_calls_skipped_by_policy"] == 0
    assert report["wrong_routes"] == 0


def test_urllib_json_post_is_injectable(monkeypatch):
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return b'{"choices": []}'
    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data)
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = urllib_json_post("http://cpu-llm:8080/v1/chat/completions", {"model": "x"})
    assert result == {"choices": []}
    assert seen == {
        "url": "http://cpu-llm:8080/v1/chat/completions",
        "timeout": 30.0,
        "body": {"model": "x"},
    }


# ═══ · jev openai compat ═════════════════════════════════════════════════════════════════════════════════
# Offline tests for the P1 OpenAI-compatible Jev backend.

def _post_with(payload: dict, captured: dict):
    def post(url: str, body: dict) -> dict:
        captured["url"] = url
        captured["body"] = body
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}
    return post


def test_noul_uses_llama_cpp_chat_completions_and_real_wire() -> None:
    captured = {}
    backend = OpenAICompatBackend(
        base_url="http://cpu-llm:8080/",
        model="tiny",
        post=_post_with({"type": "noul", "noul": 0.8}, captured),
    )
    assert backend.answer("needs_tool", noul_question(), "hello") == {"type": "noul", "noul": 0.8}
    assert backend.prob_source == PROB_SOURCE_SELF_REPORTED
    assert captured["url"] == "http://cpu-llm:8080/v1/chat/completions"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["schema"]["required"] == ["type", "noul"]


def test_choice_schema_uses_probabilities_not_legacy_distribution() -> None:
    schema = answer_schema({"type": "choice", "criteria": {"ironmate": None, "none": None}})
    assert "probabilities" in schema["properties"]
    assert "distribution" not in schema["properties"]
    assert schema["properties"]["choice"]["enum"] == ["ironmate", "none"]


def test_score_schema_has_ordinal_legend_and_probabilities() -> None:
    schema = answer_schema({"type": "score", "criteria": ["low", "high"]})
    assert schema["properties"]["legend"]["required"] == ["0", "1"]
    assert schema["properties"]["probabilities"]["required"] == ["0", "1"]


def test_invalid_model_payload_is_rejected_by_shared_wire_validator() -> None:
    backend = OpenAICompatBackend(
        base_url="http://unused",
        model="tiny",
        post=lambda url, body: {"choices": [{"message": {"content": '{"probability":0.9}'}}]},
    )
    with pytest.raises(BackendError, match="not a valid"):
        backend.answer("q", noul_question(), "state")


def test_non_json_completion_is_rejected() -> None:
    backend = OpenAICompatBackend(
        base_url="http://unused",
        model="tiny",
        post=lambda url, body: {"choices": [{"message": {"content": "yes"}}]},
    )
    with pytest.raises(BackendError, match="not valid JSON"):
        backend.answer("q", noul_question(), "state")


def test_trace_records_self_reported_probability_source(tmp_path, monkeypatch) -> None:
    log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    backend = OpenAICompatBackend(
        base_url="http://cpu-llm:8080",
        model="tiny",
        post=lambda url, body: {"choices": [{"message": {"content": '{"type":"noul","noul":0.6}'}}]},
    )
    answer_with_trace(backend, "needs_tool", noul_question(), "state")
    row = json.loads(log.read_text(encoding="utf-8"))
    assert row["backend"] == "OpenAICompatBackend"
    assert row["prob_source"] == "self_reported"


# ═══ · jev router ════════════════════════════════════════════════════════════════════════════════════════

def test_high_confidence_tool_route() -> None:
    backend = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.95},
        "tool_family": {"type": "choice", "choice": "wikipedia", "confidence": 0.9,
                        "probabilities": {"ironmate": 0.02, "wikipedia": 0.9, "mock": 0.03, "none": 0.05}},
    })
    decision = decide_route(backend, "Find a Wikipedia section")
    assert decision.needs_tool is True
    assert decision.tool_family == "wikipedia"
    assert decision.fallback_reason is None
    assert decision.confidence == 0.9


def test_low_confidence_becomes_fallback() -> None:
    backend = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.6},
        "tool_family": {"type": "choice", "choice": "ironmate", "confidence": 0.7,
                        "probabilities": {"ironmate": 0.7, "wikipedia": 0.1, "mock": 0.1, "none": 0.1}},
    })
    decision = decide_route(backend, "ambiguous")
    assert decision.fallback_reason == "low_confidence"


def test_no_tool_skips_family_question() -> None:
    backend = FixtureBackend(fixtures={"needs_tool": {"type": "noul", "noul": 0.1}})
    decision = decide_route(backend, "hello")
    assert decision.needs_tool is False
    assert decision.tool_family == "none"
    assert decision.fallback_reason is None


def test_route_decision_is_traced(tmp_path, monkeypatch) -> None:
    log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    backend = FixtureBackend(fixtures={"needs_tool": {"type": "noul", "noul": 0.1}})
    decide_route(backend, "hello", debug={"chat_id": "chat_1"})
    row = json.loads(log.read_text(encoding="utf-8"))
    assert row["event"] == "jev/route_decision"
    assert row["prob_source"] == "fixture"
    assert row["chat_id"] == "chat_1"
    assert row["decision_ms"] >= 0


def test_confusion_matrix_and_latency_are_explicit() -> None:
    rows = [
        {"tool_family": "wikipedia", "expected_tool_family": "wikipedia", "fallback_reason": None,
         "decision_ms": 1, "llm_ms": 0, "tool_ms": 5, "prob_source": "fixture", "confidence": .9},
        {"tool_family": "ironmate", "expected_tool_family": "mock", "fallback_reason": "low_confidence",
         "decision_ms": 2, "llm_ms": 10, "tool_ms": 0, "prob_source": "fixture", "confidence": .6},
    ]
    assert confusion_matrix(rows) == {
        "routed-correct": 1, "routed-wrong": 0, "fallback-correct": 0, "fallback-wrong": 1
    }
    assert latency_summary(rows) == {"decision_ms": 3.0, "llm_ms": 10.0, "tool_ms": 5.0}


def test_calibration_never_pools_probability_sources() -> None:
    rows = [
        {"tool_family": "none", "expected_tool_family": "none", "prob_source": "fixture", "confidence": .8},
        {"tool_family": "none", "expected_tool_family": "ironmate", "prob_source": "self_reported", "confidence": .8},
    ]
    result = calibration(rows)
    assert set(result) == {"fixture", "self_reported"}
    assert result["fixture"]["n"] == 1
    assert result["self_reported"]["n"] == 1


def test_none_from_tool_family_is_contradiction_fallback() -> None:
    backend = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.95},
        "tool_family": {"type": "choice", "choice": "none", "confidence": 0.9,
                        "probabilities": {"ironmate": 0.02, "wikipedia": 0.03, "mock": 0.05, "none": 0.9}},
    })
    decision = decide_route(backend, "ambiguous tool request")
    assert decision.needs_tool is True
    assert decision.tool_family == "none"
    assert decision.fallback_reason == "needs_tool_but_no_family"


def test_specific_none_family_reason_beats_low_confidence() -> None:
    backend = FixtureBackend(fixtures={
        "needs_tool": {"type": "noul", "noul": 0.95},
        "tool_family": {"type": "choice", "choice": "none", "confidence": 0.6,
                        "probabilities": {"ironmate": 0.1, "wikipedia": 0.1, "mock": 0.2, "none": 0.6}},
    })
    decision = decide_route(backend, "uncertain contradictory route")
    assert decision.confidence == 0.6
    assert decision.fallback_reason == "needs_tool_but_no_family"


# ═══ · jev shim ══════════════════════════════════════════════════════════════════════════════════════════
# Offline jev_shim fixtures -- no sockets, no API keys, no relation to any
# third-party "Jev" product (see the module docstring).

def _cases() -> dict[str, dict]:
    return {case["id"]: case for case in iter_cases()}


def test_shipped_fixtures_all_pass() -> None:
    audits = replay_fixtures()
    assert len(audits) == 5
    assert all(audit["verdict"] == VERDICT_PASS for audit in audits)


def test_five_expected_fixture_ids_present() -> None:
    ids = set(_cases())
    assert ids == {
        "noul_well_calibrated_true",
        "noul_overconfident_wrong",
        "noul_malformed_output",
        "choice_correct",
        "score_close_estimate",
    }


# --- parse_completion -------------------------------------------------


def test_parse_completion_valid_json() -> None:
    assert parse_completion_jev_shim('{"type": "noul", "noul": 0.5}') == {"type": "noul", "noul": 0.5}


def test_parse_completion_free_text_is_none() -> None:
    assert parse_completion_jev_shim("I think probably yes.") is None


def test_parse_completion_empty_string_is_none() -> None:
    assert parse_completion_jev_shim("") is None


# --- validate_noul_payload ----------------------------------------------
#
# Shapes below match TypeSafe's real /v1/systemone wire (see jev_shim.py's
# module docstring and docs/jev-shim.md), not the original guessed shape.


def test_noul_payload_valid() -> None:
    assert validate_noul_payload({"type": "noul", "noul": 0.0}) is True
    assert validate_noul_payload({"type": "noul", "noul": 1.0}) is True
    assert validate_noul_payload({"type": "noul", "noul": 0.42}) is True


def test_noul_payload_rejects_out_of_range() -> None:
    assert validate_noul_payload({"type": "noul", "noul": 1.5}) is False
    assert validate_noul_payload({"type": "noul", "noul": -0.1}) is False


def test_noul_payload_rejects_wrong_shape() -> None:
    assert validate_noul_payload({"type": "noul", "noul": 0.5, "extra": 1}) is False
    assert validate_noul_payload({}) is False
    assert validate_noul_payload({"type": "noul", "noul": "0.5"}) is False
    assert validate_noul_payload({"type": "noul", "noul": True}) is False  # bool is not a probability
    assert validate_noul_payload({"type": "choice", "noul": 0.5}) is False  # wrong type tag
    assert validate_noul_payload({"noul": 0.5}) is False  # missing type
    assert validate_noul_payload("not a dict") is False
    assert validate_noul_payload(None) is False


# --- validate_choice_payload ---------------------------------------------


def test_choice_payload_valid() -> None:
    payload = {"type": "choice", "choice": "a", "confidence": 0.6, "probabilities": {"a": 0.6, "b": 0.4}}
    assert validate_choice_payload(payload) is True


def test_choice_payload_rejects_choice_not_in_probabilities() -> None:
    payload = {"type": "choice", "choice": "c", "confidence": 0.6, "probabilities": {"a": 0.6, "b": 0.4}}
    assert validate_choice_payload(payload) is False


def test_choice_payload_rejects_probabilities_not_summing_to_one() -> None:
    payload = {"type": "choice", "choice": "a", "confidence": 0.6, "probabilities": {"a": 0.6, "b": 0.1}}
    assert validate_choice_payload(payload) is False


def test_choice_payload_rejects_missing_keys() -> None:
    assert validate_choice_payload({"type": "choice", "choice": "a"}) is False


def test_choice_payload_rejects_wrong_type_tag() -> None:
    payload = {"type": "noul", "choice": "a", "confidence": 0.6, "probabilities": {"a": 1.0}}
    assert validate_choice_payload(payload) is False


# --- validate_score_payload ----------------------------------------------


def test_score_payload_valid() -> None:
    payload = {
        "type": "score",
        "score": 0.8,
        "confidence": 0.5,
        "legend": {"0": "low", "1": "high"},
        "probabilities": {"0": 0.2, "1": 0.8},
    }
    assert validate_score_payload(payload) is True


def test_score_payload_rejects_non_numeric_score() -> None:
    payload = {
        "type": "score",
        "score": "high",
        "confidence": 0.5,
        "legend": {"0": "low", "1": "high"},
        "probabilities": {"0": 0.2, "1": 0.8},
    }
    assert validate_score_payload(payload) is False


def test_score_payload_rejects_bad_confidence() -> None:
    payload = {
        "type": "score",
        "score": 0.8,
        "confidence": 1.2,
        "legend": {"0": "low", "1": "high"},
        "probabilities": {"0": 0.2, "1": 0.8},
    }
    assert validate_score_payload(payload) is False


def test_score_payload_rejects_non_ordinal_legend_keys() -> None:
    """Real TypeSafe scores are an ordered rubric list; legend keys are "0", "1", ... ."""
    payload = {
        "type": "score",
        "score": 0.8,
        "confidence": 0.5,
        "legend": {"low": "low", "high": "high"},
        "probabilities": {"low": 0.2, "high": 0.8},
    }
    assert validate_score_payload(payload) is False


def test_score_payload_rejects_probabilities_keys_not_matching_legend() -> None:
    payload = {
        "type": "score",
        "score": 0.8,
        "confidence": 0.5,
        "legend": {"0": "low", "1": "high"},
        "probabilities": {"0": 0.2, "2": 0.8},
    }
    assert validate_score_payload(payload) is False


# --- brier_score -----------------------------------------------------------


def test_brier_score_perfect_forecaster() -> None:
    assert brier_score([(1.0, True), (0.0, False)]) == 0.0


def test_brier_score_uninformative_always_half() -> None:
    assert brier_score([(0.5, True), (0.5, False)]) == pytest.approx(0.25)


def test_brier_score_maximally_wrong() -> None:
    assert brier_score([(1.0, False)]) == pytest.approx(1.0)


def test_brier_score_empty_is_none() -> None:
    assert brier_score([]) is None


# --- replay_case per kind ---------------------------------------------


def test_replay_noul_well_calibrated() -> None:
    audit = replay_case(_cases()["noul_well_calibrated_true"])
    assert audit["schema_valid"] is True
    assert audit["predicted_probability"] == 0.92
    assert audit["correct"] is True


def test_replay_noul_overconfident_wrong_is_schema_valid_but_incorrect() -> None:
    audit = replay_case(_cases()["noul_overconfident_wrong"])
    assert audit["schema_valid"] is True
    assert audit["correct"] is False


def test_replay_noul_malformed_output_is_schema_invalid() -> None:
    audit = replay_case(_cases()["noul_malformed_output"])
    assert audit["schema_valid"] is False
    assert audit["correct"] is None
    assert audit["predicted_probability"] is None


def test_replay_choice_correct() -> None:
    audit = replay_case(_cases()["choice_correct"])
    assert audit["schema_valid"] is True
    assert audit["predicted_choice"] == "ports_to_python"
    assert audit["correct"] is True
    assert audit["confidence"] == 0.75


def test_replay_score_records_error_not_pass_fail() -> None:
    audit = replay_case(_cases()["score_close_estimate"])
    assert audit["schema_valid"] is True
    assert audit["predicted_score"] == 2.3
    assert audit["error"] == pytest.approx(0.2)
    assert audit["correct"] is None  # score has no correct/incorrect notion


# --- calibration_summary ----------------------------------------------


def test_calibration_summary_over_shipped_fixtures() -> None:
    cases = iter_cases()
    audits = [replay_case(case) for case in cases]
    summary = calibration_summary(cases, audits)
    # Only the two schema-valid noul cases count: 0.92/True and 0.97/False.
    assert summary["n"] == 2
    expected = ((0.92 - 1.0) ** 2 + (0.97 - 0.0) ** 2) / 2
    assert summary["brier_score"] == pytest.approx(expected)


def test_calibration_summary_ignores_schema_invalid_noul_case() -> None:
    # noul_malformed_output is schema-invalid and must not silently count as
    # a (0, ground_truth) forecast -- it should be excluded, not scored.
    cases = iter_cases()
    audits = [replay_case(case) for case in cases]
    invalid_ids = {a["id"] for a in audits if a["kind"] == "noul" and not a["schema_valid"]}
    assert "noul_malformed_output" in invalid_ids
    summary = calibration_summary(cases, audits)
    assert summary["n"] == 2  # not 3


def test_calibration_summary_empty_when_no_noul_cases() -> None:
    assert calibration_summary([], []) == {"brier_score": None, "n": 0}


# --- CLI -----------------------------------------------------------------


def test_main_replay_exits_zero_when_all_pass(tmp_path: Path) -> None:
    out = tmp_path / "audit.jsonl"
    code = main(["replay", "--out", str(out)])
    assert code == 0
    assert out.exists()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5


def test_main_no_subcommand_prints_help_and_exits_two() -> None:
    assert main([]) == 2


def test_default_fixture_dir_is_under_fixtures_jev_shim() -> None:
    assert DEFAULT_FIXTURE_DIR_jev_shim.name == "jev_shim"
    assert DEFAULT_FIXTURE_DIR_jev_shim.parent.name == "fixtures"


# ═══ · jev typesafe ══════════════════════════════════════════════════════════════════════════════════════
# jev_typesafe: request/response modeling for TypeSafe's real /v1/systemone.
#
# Fake-post tests prove request-building and response-parsing logic without a
# live API key. test_urllib_post_against_real_loopback_server proves the
# stdlib HTTP transport itself works, using a real socket -- a different
# claim from proving TypeSafe's real server behaves as documented (see the
# module docstring).

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
    assert validate_payload_jev_backend("choice", answers["tone"]) is True


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
        client = TypeSafeClient(base_url=base_url, api_key="sk-loopback", post=urllib_post_jev_typesafe)
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
