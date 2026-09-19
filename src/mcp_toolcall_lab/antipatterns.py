"""Classify and record LibreChat → MCP smoke observations.

The Docker + Playwright job finishes chat input and Send first. Whether MCP
then returns is recorded separately: a miss is an anti-pattern to accumulate,
not a reason to pretend the UI click never happened.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.mock.common import read_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "fixtures" / "antipatterns" / "catalog.yaml"

VERDICT_PASS = "PASS"
VERDICT_ANTIPATTERN = "ANTIPATTERN"

# Stable IDs — keep in sync with fixtures/antipatterns/catalog.yaml
SELECTOR_MISS = "SELECTOR_MISS"
AUTH_BLOCKED = "AUTH_BLOCKED"
SEND_NOT_CLICKED = "SEND_NOT_CLICKED"
TIMEOUT = "TIMEOUT"
MCP_PICKER_OFF = "MCP_PICKER_OFF"
MCP_NOT_CALLED = "MCP_NOT_CALLED"
MCP_ERROR = "MCP_ERROR"
UI_NO_RESULT = "UI_NO_RESULT"
MCP_UNREACHABLE = "MCP_UNREACHABLE"
CPU_LLM_UNREACHABLE = "CPU_LLM_UNREACHABLE"
CPU_LLM_COMPLETION_FAILED = "CPU_LLM_COMPLETION_FAILED"
CPU_LLM_GGUF_FETCH_FAILED = "CPU_LLM_GGUF_FETCH_FAILED"

KNOWN_IDS = frozenset(
    {
        SELECTOR_MISS,
        AUTH_BLOCKED,
        SEND_NOT_CLICKED,
        TIMEOUT,
        MCP_PICKER_OFF,
        MCP_NOT_CALLED,
        MCP_ERROR,
        UI_NO_RESULT,
        MCP_UNREACHABLE,
        CPU_LLM_UNREACHABLE,
        CPU_LLM_COMPLETION_FAILED,
        CPU_LLM_GGUF_FETCH_FAILED,
    }
)


def classify_observation(
    *,
    input_found: bool,
    send_clicked: bool,
    logged_in: bool,
    assistant_visible: bool,
    openai_saw_tools: bool | None,
    mcp_calls: list[dict[str, Any]],
    ui_text: str,
    expected_ui_fragment: str = "Yokohama",
) -> dict[str, Any]:
    """Return a verdict plus antipattern_id (None on PASS)."""
    if not logged_in:
        return _anti(AUTH_BLOCKED, "register/login did not reach a chat session")
    if not input_found:
        return _anti(SELECTOR_MISS, "data-testid=text-input was not found")
    if not send_clicked:
        return _anti(SEND_NOT_CLICKED, "data-testid=send-button was not clicked")
    if not assistant_visible:
        return _anti(TIMEOUT, "send clicked but no assistant message appeared")
    if openai_saw_tools is False:
        return _anti(
            MCP_PICKER_OFF,
            "LibreChat POSTed chat/completions without tools — MCP picker likely off",
        )
    if not mcp_calls:
        return _anti(MCP_NOT_CALLED, "send succeeded but MCP_TOOLCALL_LOG has no tools/call")
    if any(call.get("outcome") == "error" for call in mcp_calls):
        return _anti(MCP_ERROR, "MCP tools/call ran and recorded outcome=error")
    if expected_ui_fragment.lower() not in ui_text.lower():
        return _anti(
            UI_NO_RESULT,
            f"MCP returned but the chat UI did not show {expected_ui_fragment!r}",
        )
    return {
        "verdict": VERDICT_PASS,
        "antipattern_id": None,
        "detail": "chat send reached MCP and the UI showed the mock result",
    }


def _anti(antipattern_id: str, detail: str) -> dict[str, Any]:
    return {
        "verdict": VERDICT_ANTIPATTERN,
        "antipattern_id": antipattern_id,
        "detail": detail,
    }


def classify_stub_turn(turn: dict[str, Any], *, cpu_llm_ok: bool | None = None) -> dict[str, Any]:
    """Classify one stub-front turn. A miss is an anti-pattern, not a crash."""
    case = str(turn.get("case") or "")
    assistant = str(turn.get("assistant") or "")
    if case == "MCP_SUCCESS" and "yokohama" in assistant.lower():
        result = {
            "verdict": VERDICT_PASS,
            "antipattern_id": None,
            "detail": "stub Send reached MCP and the body showed Yokohama",
            "case": case,
        }
    elif case == "MCP_UNREACHABLE":
        result = _anti(MCP_UNREACHABLE, assistant or "stub could not reach /mcp")
    elif case == "MCP_ERROR":
        result = _anti(MCP_ERROR, assistant or "stub MCP tools/call error")
    elif case == "MCP_EMPTY":
        result = _anti(UI_NO_RESULT, "MCP returned empty; UI had no Yokohama rows")
    elif case == "MCP_SUCCESS":
        result = _anti(UI_NO_RESULT, "MCP succeeded but Yokohama was not in the assistant body")
    else:
        result = _anti(MCP_NOT_CALLED, f"stub case={case or 'missing'} did not take the MCP path")
    result["case"] = case
    if cpu_llm_ok is False:
        result["cpu_llm_ok"] = False
    elif cpu_llm_ok is True:
        result["cpu_llm_ok"] = True
    return result


def write_observation(
    path: Path,
    *,
    observation: dict[str, Any],
    source: str = "librechat-docker-playwright",
) -> dict[str, Any]:
    """Append one JSONL observation. Does not rewrite history."""
    record = {
        "at": datetime.now(UTC).isoformat(),
        "source": source,
        **observation,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_mcp_log(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def openai_request_had_tools(path: Path) -> bool | None:
    """True/False if the mock logged a completions request; None if no log."""
    events = read_jsonl(path)
    if not events:
        return None
    saw_request = False
    for event in events:
        if event.get("kind") != "chat.completions":
            continue
        saw_request = True
        if event.get("tool_names"):
            return True
    if saw_request:
        return False
    return None
