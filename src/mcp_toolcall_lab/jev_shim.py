"""Offline study: can a generic LLM imitate a "structured judgment" interface?

Background (for future readers who weren't in the conversation this came
from): TypeSafe's "Jev" is a third-party model that, per its own public
description, never generates free text -- it only answers three primitive
query shapes (a yes/no probability, a weighted score + distribution, or a
classification + distribution), each with a schema-constrained response.
This module has **no relationship to Jev or TypeSafe**, calls no external
API, and makes no claim about how the real product works internally. It
only asks a narrower, answerable question: if you *prompt* an ordinary
generative LLM to answer in the same three shapes, and *validate* its
output against that shape, how well does its self-reported confidence
match reality?

Same philosophy as prompt_experiment.py: fixtures record a hypothetical
model completion (a JSON string, as if a real LLM had been prompted to
answer this way) plus the real-world ground truth, and this module judges
the recorded completion offline. Nothing here opens a socket or calls a
model -- generating *new* completions against a real backend (llama.cpp,
an OpenAI-compatible endpoint, etc.) is a separate, not-yet-built concern.

    python -m mcp_toolcall_lab.jev_shim replay --out test-results/jev-shim.jsonl

Three query kinds, matching Jev's own vocabulary for continuity with the
discussion this came from:

- ``noul``:   a yes/no proposition -> {"probability": 0.0-1.0}
- ``score``:  a rubric -> {"score": number, "distribution": {label: prob},
              "confidence": 0.0-1.0}
- ``choice``: a classification -> {"choice": str, "distribution":
              {option: prob}, "confidence": 0.0-1.0}

A response is schema-valid only if it parses as JSON and matches its
kind's shape exactly (no missing/extra top-level keys, right types,
probabilities in range). Free text, or JSON missing a required field, is
schema-invalid -- exactly the failure mode a real constrained-decoding
backend (e.g. JSON-schema-constrained sampling) would prevent, and this
offline harness cannot, since it never calls a model.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.mock.common import append_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "fixtures" / "jev_shim"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"

KIND_NOUL = "noul"
KIND_SCORE = "score"
KIND_CHOICE = "choice"
KINDS = (KIND_NOUL, KIND_SCORE, KIND_CHOICE)

AUDIT_KEYS = (
    "id",
    "kind",
    "verdict",
    "schema_valid",
    "predicted_probability",
    "predicted_choice",
    "predicted_score",
    "confidence",
    "correct",
    "error",
)


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
    # Generous tolerance: this validates shape, not that a specific model
    # normalized its own output perfectly.
    return math.isclose(total, 1.0, abs_tol=0.05)


def validate_noul_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"probability"}:
        return False
    return _is_probability(payload["probability"])


def validate_score_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"score", "distribution", "confidence"}:
        return False
    if not isinstance(payload["score"], (int, float)) or isinstance(payload["score"], bool):
        return False
    if not _is_distribution(payload["distribution"]):
        return False
    return _is_probability(payload["confidence"])


def validate_choice_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"choice", "distribution", "confidence"}:
        return False
    if not isinstance(payload["choice"], str) or not payload["choice"]:
        return False
    distribution = payload["distribution"]
    if not _is_distribution(distribution):
        return False
    if payload["choice"] not in distribution:
        return False
    return _is_probability(payload["confidence"])


_VALIDATORS = {
    KIND_NOUL: validate_noul_payload,
    KIND_SCORE: validate_score_payload,
    KIND_CHOICE: validate_choice_payload,
}


def parse_completion(raw_completion: str) -> Any | None:
    """Parse a recorded raw model completion. ``None`` on any non-JSON text."""
    try:
        return json.loads(raw_completion)
    except (json.JSONDecodeError, TypeError):
        return None


def validate_payload(kind: str, payload: Any) -> bool:
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise ValueError(f"unknown kind: {kind!r}")
    return validator(payload)


def brier_score(predictions: list[tuple[float, bool]]) -> float | None:
    """Mean squared error between a declared probability and the 0/1 outcome.

    0.0 is a perfect forecaster; 0.25 is what an uninformative "always 0.5"
    forecaster scores; 1.0 is a maximally confident, maximally wrong one.
    ``None`` for an empty input (no forecasts to score).
    """
    if not predictions:
        return None
    total = sum((probability - (1.0 if outcome else 0.0)) ** 2 for probability, outcome in predictions)
    return total / len(predictions)


def load_case(path: Path) -> dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(case, dict):
        raise ValueError(f"{path} is not a JSON object")
    if case.get("kind") not in KINDS:
        raise ValueError(f"{path}: kind must be one of {KINDS}, got {case.get('kind')!r}")
    return case


def iter_cases(directory: Path | None = None) -> list[dict[str, Any]]:
    folder = directory or DEFAULT_FIXTURE_DIR
    cases = [load_case(path) for path in sorted(folder.glob("*.json"))]
    if not cases:
        raise FileNotFoundError(f"no jev_shim fixtures in {folder}")
    return cases


def replay_case(case: dict[str, Any]) -> dict[str, Any]:
    """Judge one fixture offline. Parses and validates the recorded completion only."""
    kind = case["kind"]
    payload = parse_completion(case.get("raw_completion", ""))
    schema_valid = payload is not None and validate_payload(kind, payload)

    predicted_probability: float | None = None
    predicted_choice: str | None = None
    predicted_score: float | None = None
    confidence: float | None = None
    correct: bool | None = None
    error: float | None = None

    if schema_valid:
        if kind == KIND_NOUL:
            predicted_probability = float(payload["probability"])
            ground_truth = bool(case["ground_truth"])
            correct = (predicted_probability >= 0.5) == ground_truth
        elif kind == KIND_CHOICE:
            predicted_choice = str(payload["choice"])
            confidence = float(payload["confidence"])
            correct = predicted_choice == case["ground_truth"]
        elif kind == KIND_SCORE:
            predicted_score = float(payload["score"])
            confidence = float(payload["confidence"])
            error = abs(predicted_score - float(case["ground_truth"]))

    audit = {
        "id": case.get("id"),
        "kind": kind,
        "schema_valid": schema_valid,
        "predicted_probability": predicted_probability,
        "predicted_choice": predicted_choice,
        "predicted_score": predicted_score,
        "confidence": confidence,
        "correct": correct,
        "error": error,
    }
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    matched_expected = all(audit.get(key) == value for key, value in expected.items())
    audit["verdict"] = VERDICT_PASS if (not expected or matched_expected) else VERDICT_FAIL
    return audit


def calibration_summary(cases: list[dict[str, Any]], audits: list[dict[str, Any]]) -> dict[str, Any]:
    """Brier score over the schema-valid ``noul`` cases only.

    Takes ``cases`` (for ``ground_truth``) alongside ``audits`` (for the
    parsed ``predicted_probability``) rather than deriving ground truth
    from ``correct``, which is itself already a thresholded (>= 0.5)
    comparison and would double-apply the threshold.
    """
    ground_truth_by_id = {case.get("id"): bool(case["ground_truth"]) for case in cases if case.get("kind") == KIND_NOUL}
    predictions = [
        (audit["predicted_probability"], ground_truth_by_id[audit["id"]])
        for audit in audits
        if audit["kind"] == KIND_NOUL and audit["schema_valid"] and audit["id"] in ground_truth_by_id
    ]
    return {"brier_score": brier_score(predictions), "n": len(predictions)}


def write_audit(path: Path, audits: list[dict[str, Any]]) -> Path:
    if path.exists():
        path.unlink()
    for audit in audits:
        append_jsonl(path, {key: audit.get(key) for key in AUDIT_KEYS})
    return path


def replay_fixtures(directory: Path | None = None, *, out: Path | None = None) -> list[dict[str, Any]]:
    cases = iter_cases(directory)
    audits = [replay_case(case) for case in cases]
    if out is not None:
        write_audit(out, audits)
    return audits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mcp_toolcall_lab.jev_shim")
    sub = parser.add_subparsers(dest="cmd")
    replay = sub.add_parser("replay", help="judge shipped fixtures offline and write audit JSONL")
    replay.add_argument("--fixtures", default=str(DEFAULT_FIXTURE_DIR))
    replay.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.cmd != "replay":
        parser.print_help()
        return 2
    cases = iter_cases(Path(args.fixtures))
    audits = [replay_case(case) for case in cases]
    if args.out:
        write_audit(Path(args.out), audits)
    print(json.dumps({"audits": audits, "calibration": calibration_summary(cases, audits)}, ensure_ascii=False, indent=2))
    return 0 if all(row.get("verdict") == VERDICT_PASS for row in audits) else 1


if __name__ == "__main__":
    raise SystemExit(main())
