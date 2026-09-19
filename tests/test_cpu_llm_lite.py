from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
if str(DEMOS) not in sys.path:
    sys.path.insert(0, str(DEMOS))

import cpu_llm_lite as cpu  # noqa: E402


def test_cpu_lite_emits_tool_call_for_yokohama() -> None:
    message = cpu.decide(
        {
            "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
            "tools": [{"type": "function", "function": {"name": "find_municipalities"}}],
        }
    )
    assert message["tool_calls"][0]["function"]["name"] == "find_municipalities"


def test_cpu_lite_plain_reply_without_tools() -> None:
    message = cpu.decide({"messages": [{"role": "user", "content": "hello"}]})
    assert message.get("tool_calls") is None
    assert "lab-cpu-lite" in message["content"]
