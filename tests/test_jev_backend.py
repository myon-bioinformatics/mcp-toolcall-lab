"""jev_backend: fixture + typesafe_mock backend-switch skeleton (#36/#37).

No sockets, no TYPESAFE_API_KEY -- both backends are offline/deterministic,
matching this repo's "default pytest stays network-free" convention."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_toolcall_lab.jev_backend import (
    DEFAULT_FIXTURE_DIR,
    PROB_SOURCE_FIXTURE,
    PROB_SOURCE_KEYWORD,
    PROB_SOURCE_TYPESAFE_MOCK,
    BackendError,
    FixtureBackend,
    KeywordBackend,
    TypeSafeMockBackend,
    answer_with_trace,
)
from mcp_toolcall_lab.jev_shim import validate_payload
from mcp_toolcall_lab.jev_typesafe import noul_question


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
        assert validate_payload(answer["type"], answer) is True


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
    assert validate_payload("noul", noul) and noul["noul"] == pytest.approx(0.875)
    choice = backend.answer("tool_family", {"criteria": {"pokeapi": None, "none": None}}, "pokemon")
    assert validate_payload("choice", choice) and choice["choice"] == "pokeapi"
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

