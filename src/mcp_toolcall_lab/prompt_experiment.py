"""Offline prompt/model experiment replay.

Inputs are official OpenAI Chat Completions ``tools`` / ``tool_calls`` plus
recorded MCP Streamable HTTP hops (JSON-RPC 2.0 request/response on
``POST /mcp``). Handshake is ``initialize`` → ``notifications/initialized``
→ ``tools/list``; a selected tool is ``tools/call``. Nothing here opens a
socket, calls a model, or talks to MLIT.

Audit JSONL is comparison fields only — not a wire log and not a substitute
for the HTTP/JSON-RPC records in the fixtures.

    python -m mcp_toolcall_lab.prompt_experiment replay --out test-results/prompt-experiments.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.mcp_http import extract_sse_data
from mcp_toolcall_lab.mock.common import append_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "fixtures" / "prompt_experiments"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"

MCP_INITIALIZE = "initialize"
MCP_INITIALIZED = "notifications/initialized"
MCP_TOOLS_LIST = "tools/list"
MCP_TOOLS_CALL = "tools/call"

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
        followup = (case.get("openai") or {}).get("followup") or {}
        follow_req = followup.get("request") if isinstance(followup, dict) else None
        if isinstance(follow_req, dict):
            follow_messages = list(follow_req.get("messages") or [])
            if follow_messages and follow_messages[0].get("role") == "system":
                content = follow_messages[0].get("content")
                if content in {None, "", rel}:
                    follow_messages[0] = {**follow_messages[0], "content": text}
            follow_req["messages"] = follow_messages
            followup["request"] = follow_req
            case["openai"]["followup"] = followup
    return case


def iter_cases(directory: Path | None = None) -> list[dict[str, Any]]:
    folder = directory or DEFAULT_FIXTURE_DIR
    cases = [load_case(path) for path in sorted(folder.glob("*.json"))]
    if not cases:
        raise FileNotFoundError(f"no prompt experiment fixtures in {folder}")
    return cases


def hop_http(hop: dict[str, Any]) -> dict[str, Any]:
    http = hop.get("http")
    return http if isinstance(http, dict) else hop


def hop_request(hop: dict[str, Any]) -> dict[str, Any]:
    request = hop_http(hop).get("request")
    return request if isinstance(request, dict) else {}


def hop_response(hop: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON-RPC response object (from ``response`` or SSE ``data:``)."""
    http = hop_http(hop)
    response = http.get("response")
    if isinstance(response, dict):
        return response
    sse = http.get("response_sse")
    if isinstance(sse, str) and sse.strip():
        parsed = extract_sse_data(sse)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def hop_method(hop: dict[str, Any]) -> str:
    return str(hop_request(hop).get("method") or "")


def hop_was_sent(hop: dict[str, Any]) -> bool:
    return hop.get("sent", True) is not False


def iter_mcp_hops(case: dict[str, Any], *, sent_only: bool = False) -> list[dict[str, Any]]:
    hops = [hop for hop in (case.get("mcp") or []) if isinstance(hop, dict)]
    if sent_only:
        return [hop for hop in hops if hop_was_sent(hop)]
    return hops


def mcp_listed_tools(hops: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for hop in hops:
        if hop_method(hop) != MCP_TOOLS_LIST:
            continue
        tools = (hop_response(hop).get("result") or {}).get("tools")
        if not isinstance(tools, list):
            continue
        for item in tools:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
    return names


def mcp_call_hops(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [hop for hop in hops if hop_was_sent(hop) and hop_method(hop) == MCP_TOOLS_CALL]


def tools_call_outcome(hop: dict[str, Any]) -> str | None:
    result = hop_response(hop).get("result")
    if not isinstance(result, dict):
        return None
    if result.get("isError"):
        return "error"
    structured = result.get("structuredContent")
    rows = structured.get("result") if isinstance(structured, dict) else None
    if rows == []:
        return "empty"
    return "success"


def mcp_called_tools(hops: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for hop in mcp_call_hops(hops):
        name = hop_request(hop).get("params", {}).get("name") if isinstance(hop_request(hop).get("params"), dict) else None
        if name:
            names.append(str(name))
    return names


def mcp_call_outcomes(hops: list[dict[str, Any]]) -> list[str]:
    return [outcome for hop in mcp_call_hops(hops) if (outcome := tools_call_outcome(hop))]


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
        calls.append(
            {
                "id": item.get("id"),
                "name": str(name),
                "arguments": parsed,
                "raw_arguments": raw_args,
            }
        )
    return calls


def tool_result_messages(openai: dict[str, Any]) -> list[dict[str, Any]]:
    """``role: tool`` rows on the follow-up Chat Completions request (the return path)."""
    followup = openai.get("followup") if isinstance(openai.get("followup"), dict) else {}
    request = followup.get("request") if isinstance(followup.get("request"), dict) else {}
    messages: list[dict[str, Any]] = []
    for item in request.get("messages") or []:
        if isinstance(item, dict) and item.get("role") == "tool":
            messages.append(item)
    return messages


def request_header(hop: dict[str, Any], name: str) -> str | None:
    headers = hop_http(hop).get("request_headers")
    if not isinstance(headers, dict):
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            text = str(value).strip()
            return text or None
    return None


def response_header(hop: dict[str, Any], name: str) -> str | None:
    headers = hop_http(hop).get("response_headers")
    if not isinstance(headers, dict):
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            text = str(value).strip()
            return text or None
    return None


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
    hops = iter_mcp_hops(case, sent_only=True)
    advertised = openai_advertised_tools(request)
    listed = mcp_listed_tools(hops)
    calls = selected_tool_calls(completion)
    selected = [item["name"] for item in calls]
    fictional = [name for name in selected if name not in advertised]
    called = mcp_called_tools(hops)
    fictional.extend(name for name in called if name not in advertised and name not in fictional)
    choices = completion.get("choices") or []
    finish = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
    schema_flags = [raw_schema_valid(item["name"], item["arguments"], request) for item in calls]
    raw_valid: bool | None = all(schema_flags) if schema_flags else None
    outcomes = mcp_call_outcomes(hops)
    if outcomes:
        server_accepted = all(row != "error" for row in outcomes)
        outcome = outcomes[0]
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
