#!/usr/bin/env python3
"""Hit the stub compose stack and append anti-pattern JSONL. Stdlib only."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mcp_toolcall_lab.antipatterns import (  # noqa: E402
    CPU_LLM_COMPLETION_FAILED,
    CPU_LLM_UNREACHABLE,
    MCP_ERROR,
    MCP_NOT_CALLED,
    MCP_UNREACHABLE,
    UI_NO_RESULT,
    classify_stub_turn,
    write_observation,
)

STUB = os.environ.get("STUB_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
CPU = os.environ.get("CPU_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
PROMPT = os.environ.get("STUB_CHAT_MESSAGE", "Find municipalities named Yokohama")
# "lite-stub" (demos/cpu_llm_lite.py, always available) or "real-gguf"
# (docker-compose.gguf.yml overlay, opt-in — see docs/stub_pages.md).
CPU_LLM_BACKEND = os.environ.get("CPU_LLM_BACKEND", "lite-stub")
OUT = Path(os.environ.get("STUB_LAST_RUN", str(ROOT / "test-results" / "last-run.json")))
ANTI = Path(os.environ.get("ANTIPATTERN_LOG", str(ROOT / "test-results" / "antipatterns.jsonl")))


def _get(url: str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return 0, str(exc)


def _post_json(url: str, payload: dict, timeout: float = 20.0) -> tuple[int, dict | str]:
    raw = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=raw, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return 0, str(exc)


def _cpu_completion_ok() -> tuple[bool, str]:
    """Hit /v1/chat/completions for real, not just /health. A process can be
    up (health 200) while inference itself is broken (OOM, bad model args,
    a real GGUF that never finished loading) — that gap is the point of a
    separate check. Works unmodified against both the lite stand-in
    (demos/cpu_llm_lite.py) and a real llama.cpp server, since both speak
    the same OpenAI-compatible endpoint."""
    status, body = _post_json(
        f"{CPU}/v1/chat/completions",
        {"model": "lab-cpu-lite", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 16},
        timeout=60.0,
    )
    if status != 200 or not isinstance(body, dict):
        return False, str(body)[:300]
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return False, f"no choices in response: {json.dumps(body)[:300]}"
    return True, ""


def main() -> int:
    stub_health = _get(f"{STUB}/health")
    cpu_health = _get(f"{CPU}/health")
    cpu_ok = cpu_health[0] == 200
    cpu_completion_ok, cpu_completion_detail = (False, "skipped: /health already failed")
    if cpu_ok:
        cpu_completion_ok, cpu_completion_detail = _cpu_completion_ok()
    status, turn = _post_json(f"{STUB}/api/turn", {"prompt": PROMPT})
    if not isinstance(turn, dict):
        turn = {"case": "MCP_UNREACHABLE", "assistant": str(turn), "error": True}
    observation = classify_stub_turn(turn, cpu_llm_ok=cpu_ok)
    write_observation(ANTI, observation=observation, source="stub-pages-actions")
    if not cpu_ok:
        write_observation(
            ANTI,
            observation={
                "verdict": "ANTIPATTERN",
                "antipattern_id": CPU_LLM_UNREACHABLE,
                "detail": cpu_health[1][:300],
            },
            source="stub-pages-actions",
        )
    elif not cpu_completion_ok:
        write_observation(
            ANTI,
            observation={
                "verdict": "ANTIPATTERN",
                "antipattern_id": CPU_LLM_COMPLETION_FAILED,
                "detail": cpu_completion_detail,
            },
            source="stub-pages-actions",
        )
    report = {
        "stub_health": {"status": stub_health[0], "body": stub_health[1][:200]},
        "cpu_llm_ok": cpu_ok,
        "cpu_llm_backend": CPU_LLM_BACKEND,
        "cpu_llm_completion_ok": cpu_completion_ok,
        "turn_http": status,
        "turn": turn,
        "observation": observation,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if observation.get("antipattern_id") in {MCP_UNREACHABLE, MCP_ERROR, MCP_NOT_CALLED, UI_NO_RESULT}:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
