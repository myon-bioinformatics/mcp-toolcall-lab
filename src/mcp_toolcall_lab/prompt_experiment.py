"""Offline prompt/model experiment replay.

Inputs are official OpenAI Chat Completions ``tools`` / ``tool_calls`` plus
recorded MCP Streamable HTTP events (``initialize`` / ``tools/list`` /
``tools/call``). Nothing here opens a socket, calls a model, or talks to
MLIT. Audit JSONL is comparison fields only — not a new wire log.

    python -m mcp_toolcall_lab.prompt_experiment replay --out test-results/prompt-experiments.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.mock.common import append_jsonl
from mcp_toolcall_lab.record import EVENT_TOOLS_LIST, mcp_tool_calls

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "fixtures" / "prompt_experiments"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"

AUDIT_KEYS = (
    "id",
    "verdict",
    "selected_tools",
    "fictional_tools",
    "advertised_tools",
    "mcp_listed_tools",
    "mcp_called",
    "finish_reason",
    "raw_schema_valid",
    "server_accepted",
    "outcome",
    "model",
    "reasoning",
    "stop",
    "matched_expected",
)


def load_case(path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Load one fixture and resolve ``system_prompt`` into the request messages."""
    root = repo_root or REPO_ROOT
    case = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(case, dict):
        raise ValueError(f"{path} is not a JSON object")
    rel = str(case.get("system_prompt") or "").strip()
    if rel:
        text = (root / rel).read_text(encoding="utf-8").strip()
        case["_system_prompt_text"] = text
        request = (case.get("openai") or {}).get("request") or {}
        messages = list(request.get("messages") or [])
        if messages and messages[0].get("role") == "system":
            content = messages[0].get("content")
            if content in {None, "", rel}:
                messages[0] = {**messages[0], "content": text}
        request["messages"] = messages
        case.setdefault("openai", {})["request"] = request
    return case


def iter_cases(directory: Path | None = None) -> list[dict[str, Any]]:
    folder = directory or DEFAULT_FIXTURE_DIR
    cases = [load_case(path) for path in sorted(folder.glob("*.json"))]
    if not cases:
        raise FileNotFoundError(f"no prompt experiment fixtures in {folder}")
    return cases


def openai_advertised_tools(request: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in request.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") not in {None, "function"}:
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = function.get("name")
        if name:
            names.append(str(name))
    return names


def openai_tool_parameters(request: dict[str, Any], name: str) -> dict[str, Any] | None:
    for tool in request.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if str(function.get("name") or "") == name:
            params = function.get("parameters")
            return params if isinstance(params, dict) else {}
    return None


def assistant_message(completion: dict[str, Any]) -> dict[str, Any]:
    choices = completion.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {}
    message = choices[0].get("message")
    return message if isinstance(message, dict) else {}


def selected_tool_calls(completion: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in assistant_message(completion).get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = function.get("name")
        if not name:
            continue
        raw_args = function.get("arguments", "{}")
        parsed: Any
        if isinstance(raw_args, dict):
            parsed = raw_args
        else:
            try:
                parsed = json.loads(str(raw_args))
            except json.JSONDecodeError:
                parsed = None
        calls.append({"name": str(name), "arguments": parsed, "raw_arguments": raw_args})
    return calls


def mcp_listed_tools(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        if event.get("event") != EVENT_TOOLS_LIST:
            continue
        listed = event.get("tools")
        if listed is None:
            listed = event.get("result")
        if isinstance(listed, list):
            for item in listed:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
    return names


def mcp_called_tools(events: list[dict[str, Any]]) -> list[str]:
    return [str(event["tool"]) for event in mcp_tool_calls(events)]


def _value_matches_type(value: Any, expected: str | None) -> bool:
    if not expected:
        return True
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def raw_schema_valid(name: str, arguments: Any, request: dict[str, Any]) -> bool:
    """True when raw ``tool_calls[].function.arguments`` match advertised parameters."""
    if not isinstance(arguments, dict):
        return False
    params = openai_tool_parameters(request, name)
    if params is None:
        return False
    required = [str(key) for key in params.get("required") or []]
    if any(key not in arguments for key in required):
        return False
    properties = params.get("properties") if isinstance(params.get("properties"), dict) else {}
    if params.get("additionalProperties") is False:
        if any(key not in properties for key in arguments):
            return False
    for key, value in arguments.items():
        spec = properties.get(key)
        if isinstance(spec, dict) and not _value_matches_type(value, spec.get("type")):
            return False
    return True


def replay_case(case: dict[str, Any]) -> dict[str, Any]:
    """Judge one fixture offline. Does not call OpenAI, MCP, or any HTTP API."""
    openai = case.get("openai") or {}
    request = openai.get("request") if isinstance(openai.get("request"), dict) else {}
    completion = openai.get("completion") if isinstance(openai.get("completion"), dict) else {}
    events = list(case.get("mcp") or [])
    advertised = openai_advertised_tools(request)
    listed = mcp_listed_tools(events)
    calls = selected_tool_calls(completion)
    selected = [item["name"] for item in calls]
    fictional = [name for name in selected if name not in advertised]
    called = mcp_called_tools(events)
    fictional.extend(name for name in called if name not in advertised and name not in fictional)
    choices = completion.get("choices") or []
    finish = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
    schema_flags = [raw_schema_valid(item["name"], item["arguments"], request) for item in calls]
    raw_valid: bool | None = all(schema_flags) if schema_flags else None
    call_rows = mcp_tool_calls(events)
    if call_rows:
        server_accepted = all(row.get("outcome") != "error" for row in call_rows)
        outcomes = [str(row.get("outcome")) for row in call_rows if row.get("outcome")]
        outcome = outcomes[0] if outcomes else None
    else:
        server_accepted = None
        outcome = None
    settings = case.get("settings") if isinstance(case.get("settings"), dict) else {}
    if fictional:
        verdict = VERDICT_FAIL
    elif selected:
        listed_ok = not listed or all(name in listed for name in selected)
        mcp_ok = not called or called == selected
        verdict = VERDICT_PASS if listed_ok and mcp_ok and not any(flag is False for flag in schema_flags) else VERDICT_FAIL
    else:
        extra_call = bool(called)
        verdict = VERDICT_FAIL if extra_call or finish == "tool_calls" else VERDICT_PASS

    audit = {
        "id": case.get("id"),
        "verdict": verdict,
        "selected_tools": selected,
        "fictional_tools": fictional,
        "advertised_tools": advertised,
        "mcp_listed_tools": listed,
        "mcp_called": called,
        "finish_reason": finish,
        "raw_schema_valid": raw_valid,
        "server_accepted": server_accepted,
        "outcome": outcome,
        "model": settings.get("model") or request.get("model"),
        "reasoning": settings.get("reasoning"),
        "stop": settings.get("stop", request.get("stop")),
    }
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    audit["matched_expected"] = all(audit.get(key) == expected[key] for key in expected)
    if expected and not audit["matched_expected"]:
        audit["verdict"] = VERDICT_FAIL
    return audit


def write_audit(path: Path, audits: list[dict[str, Any]]) -> Path:
    """Write comparison-only JSONL. Prompts, arguments, and ids stay off this file."""
    if path.exists():
        path.unlink()
    for audit in audits:
        append_jsonl(path, {key: audit.get(key) for key in AUDIT_KEYS})
    return path


def replay_fixtures(
    directory: Path | None = None,
    *,
    out: Path | None = None,
) -> list[dict[str, Any]]:
    audits = [replay_case(case) for case in iter_cases(directory)]
    if out is not None:
        write_audit(out, audits)
    return audits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mcp_toolcall_lab.prompt_experiment")
    sub = parser.add_subparsers(dest="cmd")
    replay = sub.add_parser("replay", help="judge shipped fixtures offline and write audit JSONL")
    replay.add_argument("--fixtures", default=str(DEFAULT_FIXTURE_DIR))
    replay.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.cmd != "replay":
        parser.print_help()
        return 2
    audits = replay_fixtures(
        Path(args.fixtures),
        out=Path(args.out) if args.out else None,
    )
    print(json.dumps(audits, ensure_ascii=False, indent=2))
    return 0 if all(row.get("matched_expected") for row in audits) else 1


if __name__ == "__main__":
    raise SystemExit(main())
