"""jev_backend: #36/#37 backend-switch skeleton -- fixture + typesafe_mock only.

Both backends are offline and deterministic (no socket, no API key). Tracing
via MCP_TOOLCALL_LOG is exercised with a tmp_path file; when the env var is
unset (the default), decide() must not write anything or require network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_toolcall_lab.jev_backend import (
    BACKEND_FIXTURE,
    BACKEND_TYPESAFE_MOCK,
    DEFAULT_FIXTURE_FILE,
    EVENT_JEV_DECISION,
    FixtureBackend,
    TypeSafeMockBackend,
    decide,
    load_fixture_answers,
    load_fixture_backend,
    make_typesafe_mock_backend,
)
from mcp_toolcall_lab.jev_shim import validate_payload
from mcp_toolcall_lab.jev_typesafe import noul_question, choice_question, score_question


def _questions() -> dict[str, dict]:
    return {
        "is_ready": noul_question(),
        "tone": choice_question({"calm": None, "angry": None, "excited": None}),
        "coverage": score_question(["no coverage", "basic happy-path only", "thorough"]),
    }


# --- FixtureBackend ----------------------------------------------------------


def test_load_fixture_answers_default_file_is_wire_valid() -> None:
    answers = load_fixture_answers()
    assert set(answers) == {"is_ready", "tone", "coverage"}
    for name, answer in answers.items():
        assert validate_payload(answer["type"], answer) is True, name


def test_load_fixture_answers_rejects_invalid_payload(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps({"is_ready": {"type": "noul", "noul": 2.0}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixture_answers(bad_file)


def test_fixture_backend_ask_returns_requested_subset() -> None:
    backend = load_fixture_backend()
    assert backend.name == BACKEND_FIXTURE
    answers = backend.ask("some state", {"is_ready": noul_question()})
    assert answers == {"is_ready": {"type": "noul", "noul": 0.87}}


def test_fixture_backend_ask_raises_on_unknown_question_name() -> None:
    backend = FixtureBackend(answers_by_name={"is_ready": {"type": "noul", "noul": 0.5}})
    with pytest.raises(KeyError):
        backend.ask("state", {"missing": noul_question()})


# --- TypeSafeMockBackend -------------------------------------------------------


def test_typesafe_mock_backend_uses_real_client_wire() -> None:
    answers_by_name = load_fixture_answers()
    backend = make_typesafe_mock_backend(answers_by_name)
    assert isinstance(backend, TypeSafeMockBackend)
    assert backend.name == BACKEND_TYPESAFE_MOCK

    answers = backend.ask("some state", {"is_ready": noul_question(), "tone": choice_question({"calm": None, "angry": None, "excited": None})})
    assert answers["is_ready"] == answers_by_name["is_ready"]
    assert answers["tone"] == answers_by_name["tone"]


def test_typesafe_mock_backend_raises_on_unknown_question_name() -> None:
    backend = make_typesafe_mock_backend({"is_ready": {"type": "noul", "noul": 0.5}})
    with pytest.raises(KeyError):
        backend.ask("state", {"missing": noul_question()})


def test_typesafe_mock_backend_never_sends_authorization_header() -> None:
    """The mock backend must not pick up a real TYPESAFE_API_KEY from the environment."""
    backend = make_typesafe_mock_backend(load_fixture_answers())
    assert backend.client.api_key is None


# --- decide(): both backends answer the same wire shape -----------------------


@pytest.mark.parametrize("backend_name", [BACKEND_FIXTURE, BACKEND_TYPESAFE_MOCK])
def test_decide_produces_schema_valid_records_for_both_backends(backend_name: str) -> None:
    answers_by_name = load_fixture_answers()
    backend = load_fixture_backend() if backend_name == BACKEND_FIXTURE else make_typesafe_mock_backend(answers_by_name)

    records = decide(backend, "some state", {"is_ready": noul_question()})

    assert len(records) == 1
    record = records[0]
    assert record["name"] == "is_ready"
    assert record["kind"] == "noul"
    assert record["backend"] == backend_name
    assert record["prob_source"] == backend_name
    assert record["schema_valid"] is True
    assert record["answer"] == answers_by_name["is_ready"]
    assert isinstance(record["latency_ms"], float)
    assert record["latency_ms"] >= 0.0


def test_decide_rejects_unknown_question_type() -> None:
    backend = load_fixture_backend()
    with pytest.raises(ValueError):
        decide(backend, "state", {"bad": {"type": "essay"}})


def test_decide_without_mcp_toolcall_log_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_TOOLCALL_LOG", raising=False)
    backend = load_fixture_backend()
    decide(backend, "state", {"is_ready": noul_question()})
    assert list(tmp_path.iterdir()) == []


def test_decide_traces_one_row_per_question_with_correlation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    backend = load_fixture_backend()

    decide(backend, "some state", _questions(), meta={"chat_id": "chat_wire"})

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert {row["name"] for row in rows} == {"is_ready", "tone", "coverage"}
    for row in rows:
        assert row["event"] == EVENT_JEV_DECISION
        assert row["chat_id"] == "chat_wire"
        assert row["debug"]["chat_id_source"] == "meta"
        assert row["prob_source"] == BACKEND_FIXTURE
        assert row["schema_valid"] is True


def test_default_fixture_file_exists() -> None:
    assert DEFAULT_FIXTURE_FILE.is_file()
