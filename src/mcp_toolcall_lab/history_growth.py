"""Read-only OpenAI mock request growth measurements (Issue #115).

Reads OPENAI_MOCK_LOG JSONL without emitting message bodies or secrets.
New logs contain observed text lengths calculated at request ingress. Older\nprojection-only logs must remain explicitly unmeasurable.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def measure(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    per_chat: dict[str, int] = defaultdict(int)
    result: list[dict[str, Any]] = []
    for line_number, row in enumerate(rows, 1):
        if row.get("kind") != "chat.completions":
            continue
        messages = row.get("wire_messages")
        if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            continue
        chat_id = row.get("chat_id")
        # Do not combine unknown chats: each untagged request is isolated.
        key = chat_id if isinstance(chat_id, str) and chat_id else f"unknown:{line_number}"
        per_chat[key] += 1
        observed = row.get("content_measurement") == "observed_request"\n        role_chars = row.get("role_content_chars")\n        valid = (observed and type(row.get("content_chars")) is int\n                 and row["content_chars"] >= 0 and type(row.get("tool_content_chars")) is int\n                 and isinstance(role_chars, dict)\n                 and set(role_chars) == {"system", "developer", "user", "assistant", "tool"}\n                 and all(type(v) is int and v >= 0 for v in role_chars.values())\n                 and row["content_chars"] == sum(role_chars.values())\n                 and row["tool_content_chars"] == role_chars["tool"])\n        result.append({
            "line": line_number,
            "chat_id": chat_id if isinstance(chat_id, str) and chat_id else None,
            "turn_index": per_chat[key],
            "completion_id": row.get("completion_id"),
            "message_count": len(messages),
            "tool_message_count": sum(m.get("role") == "tool" for m in messages),
            "projection_json_chars": len(json.dumps(messages, ensure_ascii=False)),
            "content_chars": row["content_chars"] if valid else None,\n            "role_content_chars": dict(role_chars) if valid else None,\n            "tool_content_chars": row["tool_content_chars"] if valid else None,\n            "content_measurement": ("observed_request" if valid else\n                "invalid_messages" if row.get("content_measurement") == "invalid_messages" else\n                "invalid_metrics" if observed else "unavailable_in_wire_projection"),
        })
    return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {index}: expected JSON object")
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="OPENAI_MOCK_LOG JSONL path")
    args = parser.parse_args(argv)
    for item in measure(load_jsonl(args.log)):
        print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
