"""Opt-in lossy prompt experiment with independent character/token budgets.

No model downloads or calls on import. Counts apply to the complete prompt text,
not chat-template tokens. Author-supplied candidates declare optional losses;
required anchors and task arguments are scored independently. No truncation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
import urllib.request

from .char_compress import compress


class MeasurementError(ValueError):
    """An installed tokenizer could not produce a trustworthy count."""


def _limit(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("budgets must be nonnegative integers or None")


def prompt_for(case: dict[str, Any], text: str) -> str:
    # The advertised catalogue is an input, independent of the answer oracle.
    catalog = case["advertised_tools"]
    if not isinstance(catalog, list) or len(catalog) < 2 or not all(isinstance(t, str) and t for t in catalog):
        raise ValueError("at least two advertised tool descriptions are required")
    return ("Choose one advertised tool and extract arguments from the task. "
            "Return JSON only: {\"tool\":name,\"arguments\":{...}}. "
            "Missing task: return {\"tool\":null,\"arguments\":{}}. Do not invent values.\n"
            "Tools (all arguments required except ?; use the declared JSON types):\n" + "\n".join(catalog) + "\nTask:\n" + text)


def candidates(case: dict[str, Any], counter: Callable[[str], int] | None = None) -> list[dict[str, Any]]:
    source = case["source"]
    anchors = case["required_anchors"]
    if not all(isinstance(a, str) and a and a in source for a in anchors):
        raise ValueError("required anchors must be nonempty literals in the source")
    rule_text = compress(source, mode="chars", token_counter=lambda _: 0).text
    rows = [{"id": "verbatim", "loss_rank": 0, "text": source, "losses": []},
            {"id": "rules", "loss_rank": 1, "text": rule_text,
             "losses": ["wording/abbreviations may change"]}]
    rows.extend(case.get("candidates", []))
    for row in rows:
        if type(row.get("loss_rank")) is not int or row["loss_rank"] < 0:
            raise ValueError("loss_rank must be a nonnegative integer")
        if not isinstance(row.get("text"), str) or not isinstance(row.get("losses"), list):
            raise ValueError("candidate text/losses are required")
    return sorted(rows, key=lambda r: r["loss_rank"])


def select(case: dict[str, Any], *, max_chars: int | None, max_tokens: int | None,
           token_counter: Callable[[str], int] | None = None) -> dict[str, Any]:
    _limit(max_chars); _limit(max_tokens)
    if max_chars is None and max_tokens is None:
        raise ValueError("at least one budget is required")
    attempts = []
    if max_tokens is not None and token_counter is None:
        return {"status": "tokenizer_unavailable", "attempts": attempts, "selected": None}
    for row in candidates(case, token_counter):
        missing = [a for a in case["required_anchors"] if a not in row["text"]]
        prompt = prompt_for(case, row["text"])
        try:
            tokens = token_counter(prompt) if token_counter else None
        except Exception as exc:
            raise MeasurementError(f"tokenizer failed: {type(exc).__name__}: {exc}") from exc
        if tokens is not None and (type(tokens) is not int or tokens < 0):
            raise MeasurementError("token counter must return a nonnegative integer")
        fits = not missing and (max_chars is None or len(prompt) <= max_chars) and (max_tokens is None or tokens <= max_tokens)
        trial = {"candidate": row["id"], "loss_rank": row["loss_rank"], "losses": row["losses"],
                 "chars": len(prompt), "utf8_bytes": len(prompt.encode()), "tokens": tokens,
                 "missing_anchors": missing, "fits": fits}
        attempts.append(trial)
        if fits:
            return {"status": "selected", "selected": {**trial, "prompt": prompt}, "attempts": attempts}
    return {"status": "infeasible", "selected": None, "attempts": attempts}


def judge(case: dict[str, Any], text: str) -> dict[str, Any]:
    try:
        result = json.loads(text)
        if not isinstance(result, dict) or set(result) != {"tool", "arguments"} or not isinstance(result["arguments"], dict):
            raise ValueError("expected tool/arguments object")
        args = result["arguments"]
        if result["tool"] is None:
            return {"status": "abstained", "task_success": False, "quality_score": None, "response": result}
        allowed = set(case["required_arguments"]) | set(case.get("preferred_arguments", {}))
        unknown = sorted(set(args) - allowed)
        required = all(type(args.get(k)) is type(v) and args[k] == v for k, v in case["required_arguments"].items())
        success = result["tool"] == case["tool"] and required and not unknown
        preferences = case.get("preferred_arguments", {})
        hits = sum(type(args.get(k)) is type(v) and args[k] == v for k, v in preferences.items())
        return {"status": "success" if success else "wrong_action", "task_success": success,
                "quality_hits": hits, "quality_total": len(preferences),
                "quality_score": hits / len(preferences) if preferences else None,
                "unknown_arguments": unknown, "response": result}
    except (ValueError, TypeError, KeyError):
        return {"status": "invalid_output", "task_success": False, "quality_score": None, "raw_response": text}


def evaluate(case: dict[str, Any], *, max_chars: int | None, max_tokens: int | None,
             token_counter: Callable[[str], int] | None = None,
             answer: Callable[[str], str] | None = None) -> dict[str, Any]:
    try:
        selection = select(case, max_chars=max_chars, max_tokens=max_tokens, token_counter=token_counter)
    except MeasurementError as exc:
        selection = {"status": "measurement_error", "selected": None, "attempts": [], "error": f"{type(exc).__name__}: {exc}"}
    result = {"case_id": case["id"], "language": case["language"], "max_chars": max_chars,
              "max_tokens": max_tokens, **selection}
    if result["status"] != "selected":
        result["task_success"] = None  # unavailable is not a model error or success
        return result
    if answer is None:
        return {**result, "status": "not_run", "task_success": None}
    try:
        return {**result, **judge(case, answer(result["selected"]["prompt"]))}
    except Exception as exc:
        return {**result, "status": "model_error", "task_success": None, "error": f"{type(exc).__name__}: {exc}"}


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--base-url", help="explicit local llama-server URL; no model download")
    parser.add_argument("--char-budget", type=int, nargs="+", default=[768, 2048])
    parser.add_argument("--token-budget", type=int, nargs="+", default=[256, 512])
    parser.add_argument("--model-id", default="unknown")
    parser.add_argument("--model-revision", default="unknown")
    parser.add_argument("--runtime-revision", default="unknown")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.base_url and "unknown" in {args.model_id, args.model_revision, args.runtime_revision}:
        parser.error("live runs require --model-id, --model-revision and --runtime-revision")
    raw = args.cases.read_bytes(); cases = json.loads(raw)
    base = args.base_url.rstrip("/") if args.base_url else None
    def count(text: str) -> int:
        return len(_post(base + "/tokenize", {"content": text, "add_special": False, "parse_special": False})["tokens"])
    def answer(text: str) -> str:
        response = _post(base + "/v1/chat/completions", {"messages": [{"role": "user", "content": text}], "temperature": 0,
                         "seed": 42, "max_tokens": 128, "stream": False})
        choice = response["choices"][0]
        if choice.get("finish_reason") != "stop" or not isinstance(choice["message"].get("content"), str):
            raise ValueError("model did not complete a textual answer")
        return choice["message"]["content"]
    rows = [evaluate(case, max_chars=c, max_tokens=t, token_counter=count if base else None,
                     answer=answer if base else None) for case in cases for c in args.char_budget for t in args.token_budget]
    report = {"schema_version": 1, "corpus_sha256": hashlib.sha256(raw).hexdigest(),
              "measurement": "complete_prompt_text_without_chat_template", "tokenizer": "server/tokenize" if base else "unavailable",
              "model_id": args.model_id, "model_revision": args.model_revision, "runtime_revision": args.runtime_revision,
              "seed": 42, "temperature": 0, "max_output_tokens": 128, "cases": rows,
              "summary": {"all_trials": len(rows), "task_successes": sum(r.get("task_success") is True for r in rows),
                          "task_success_rate_all_trials": sum(r.get("task_success") is True for r in rows) / len(rows) if rows else None,
                          "statuses": {s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})}}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
