"""Offline jev_shim fixtures -- no sockets, no API keys, no relation to any
third-party "Jev" product (see the module docstring)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_toolcall_lab.jev_shim import (
    DEFAULT_FIXTURE_DIR,
    VERDICT_PASS,
    brier_score,
    calibration_summary,
    iter_cases,
    main,
    parse_completion,
    replay_case,
    replay_fixtures,
    validate_choice_payload,
    validate_noul_payload,
    validate_score_payload,
)


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
    assert parse_completion('{"probability": 0.5}') == {"probability": 0.5}


def test_parse_completion_free_text_is_none() -> None:
    assert parse_completion("I think probably yes.") is None


def test_parse_completion_empty_string_is_none() -> None:
    assert parse_completion("") is None


# --- validate_noul_payload ----------------------------------------------


def test_noul_payload_valid() -> None:
    assert validate_noul_payload({"probability": 0.0}) is True
    assert validate_noul_payload({"probability": 1.0}) is True
    assert validate_noul_payload({"probability": 0.42}) is True


def test_noul_payload_rejects_out_of_range() -> None:
    assert validate_noul_payload({"probability": 1.5}) is False
    assert validate_noul_payload({"probability": -0.1}) is False


def test_noul_payload_rejects_wrong_shape() -> None:
    assert validate_noul_payload({"probability": 0.5, "extra": 1}) is False
    assert validate_noul_payload({}) is False
    assert validate_noul_payload({"probability": "0.5"}) is False
    assert validate_noul_payload({"probability": True}) is False  # bool is not a probability
    assert validate_noul_payload("not a dict") is False
    assert validate_noul_payload(None) is False


# --- validate_choice_payload ---------------------------------------------


def test_choice_payload_valid() -> None:
    payload = {"choice": "a", "distribution": {"a": 0.6, "b": 0.4}, "confidence": 0.6}
    assert validate_choice_payload(payload) is True


def test_choice_payload_rejects_choice_not_in_distribution() -> None:
    payload = {"choice": "c", "distribution": {"a": 0.6, "b": 0.4}, "confidence": 0.6}
    assert validate_choice_payload(payload) is False


def test_choice_payload_rejects_distribution_not_summing_to_one() -> None:
    payload = {"choice": "a", "distribution": {"a": 0.6, "b": 0.1}, "confidence": 0.6}
    assert validate_choice_payload(payload) is False


def test_choice_payload_rejects_missing_keys() -> None:
    assert validate_choice_payload({"choice": "a"}) is False


# --- validate_score_payload ----------------------------------------------


def test_score_payload_valid() -> None:
    payload = {"score": 7.5, "distribution": {"low": 0.2, "high": 0.8}, "confidence": 0.5}
    assert validate_score_payload(payload) is True


def test_score_payload_rejects_non_numeric_score() -> None:
    payload = {"score": "high", "distribution": {"low": 0.2, "high": 0.8}, "confidence": 0.5}
    assert validate_score_payload(payload) is False


def test_score_payload_rejects_bad_confidence() -> None:
    payload = {"score": 7.5, "distribution": {"low": 0.2, "high": 0.8}, "confidence": 1.2}
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
    assert audit["predicted_score"] == 6.0
    assert audit["error"] == pytest.approx(0.5)
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
    assert DEFAULT_FIXTURE_DIR.name == "jev_shim"
    assert DEFAULT_FIXTURE_DIR.parent.name == "fixtures"
