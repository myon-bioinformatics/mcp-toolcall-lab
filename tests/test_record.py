"""record_call() in isolation -- no server needed, just the JSONL writer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mcp_toolcall_lab.record import OUTCOME_SUCCESS, record_call


@pytest.fixture()
def log_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "toolcalls.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(path))
    return path


def _read_event(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_record_call_without_duration_omits_the_key(log_path: Path) -> None:
    record_call(tool="find_stations", arguments={}, outcome=OUTCOME_SUCCESS, result=[])
    assert "duration_ms" not in _read_event(log_path)


def test_record_call_rounds_duration_to_three_decimal_places(log_path: Path) -> None:
    record_call(tool="find_stations", arguments={}, outcome=OUTCOME_SUCCESS, result=[], duration_ms=1.23456789)
    assert _read_event(log_path)["duration_ms"] == 1.235


def test_record_call_zero_duration_is_logged_not_treated_as_absent(log_path: Path) -> None:
    # duration_ms=0.0 is falsy but a real, meaningful measurement (an
    # implausibly fast call, not "no measurement was taken") -- must use
    # `is not None`, not a truthiness check, to decide whether to log it.
    record_call(tool="find_stations", arguments={}, outcome=OUTCOME_SUCCESS, result=[], duration_ms=0.0)
    assert _read_event(log_path)["duration_ms"] == 0.0


def test_record_call_without_meta_omits_the_key(log_path: Path) -> None:
    record_call(tool="find_stations", arguments={}, outcome=OUTCOME_SUCCESS, result=[], meta=None)
    assert "meta" not in _read_event(log_path)


def test_record_call_with_empty_meta_dict_omits_the_key(log_path: Path) -> None:
    record_call(tool="find_stations", arguments={}, outcome=OUTCOME_SUCCESS, result=[], meta={})
    assert "meta" not in _read_event(log_path)
