"""Conservative, provenance-aware conversation compaction.

No tool-produced text is ever promoted to a user/system instruction.
The typed digest is retained out-of-band, not serialized to the model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class CompactionResult:
    messages: list[dict[str, Any]]
    provenance: dict[str, Any] | None
    compacted: bool


def estimate(messages: Sequence[Mapping[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False, default=str)) // 3


def _safe_cut(body: Sequence[Mapping[str, Any]], keep: int) -> int:
    if keep < 1:
        raise ValueError("keep must be positive")
    cut = max(0, len(body) - keep)
    # Start the retained suffix at a new user turn, never within a tool exchange.
    while cut < len(body) and body[cut].get("role") != "user":
        cut += 1
    return cut


def _typed_provenance(head: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep trusted and untrusted origins in distinct structured fields."""
    record: dict[str, Any] = {
        "schema_version": 1,
        "user_messages": [],
        "assistant_decisions": [],
        "tool_calls": [],
        "tool_results": [],
    }
    calls: set[str] = set()
    results: set[str] = set()
    for m in head:
        role = m.get("role")
        if role == "user":
            record["user_messages"].append({"content": m.get("content")})
        elif role == "assistant":
            record["assistant_decisions"].append({"content": m.get("content")})
            for call in m.get("tool_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    raise ValueError("invalid tool call provenance")
                if call["id"] in calls:
                    raise ValueError("duplicate tool call id")
                calls.add(call["id"])
                record["tool_calls"].append({
                    "tool_call_id": call["id"], "function": call.get("function"),
                })
        elif role == "tool":
            identifier = m.get("tool_call_id")
            if not isinstance(identifier, str) or identifier not in calls or identifier in results:
                raise ValueError("orphan or duplicate tool result")
            results.add(identifier)
            record["tool_results"].append({
                "tool_call_id": identifier, "content": m.get("content"),
                "trust": "untrusted", "source_role": "tool",
            })
        else:
            raise ValueError(f"unsupported role in compacted history: {role!r}")
    if calls != results:
        raise ValueError("incomplete tool exchange")
    return record


def compact(
    messages: list[dict[str, Any]], *, budget: int, keep: int = 6,
    count: Callable[[Sequence[Mapping[str, Any]]], int] = estimate,
) -> CompactionResult:
    """Compact only when the removed prefix can be represented safely.

    No free-text digest is submitted as a model message. To avoid losing
    essential previous user instructions, compaction is rejected if the
    removed prefix contains user turns. Callers can retain the typed
    provenance separately for auditing or an explicitly trusted renderer.
    """
    if budget < 0:
        raise ValueError("budget must not be negative")
    if keep < 1:
        raise ValueError("keep must be positive")
    if count(messages) <= budget:
        return CompactionResult(messages, None, False)
    prefix = 1 if messages and messages[0].get("role") == "system" else 0
    body = messages[prefix:]
    cut = _safe_cut(body, keep)
    if cut == 0 or cut >= len(body):
        return CompactionResult(messages, None, False)
    removed = body[:cut]
    # Safety first: a removed user turn might still contain live constraints.
    # Do not silently replace it with an assistant, system, or user summary.
    if any(m.get("role") == "user" for m in removed):
        return CompactionResult(messages, None, False)
    provenance = _typed_provenance(removed)
    return CompactionResult(messages[:prefix] + body[cut:], provenance, True)
