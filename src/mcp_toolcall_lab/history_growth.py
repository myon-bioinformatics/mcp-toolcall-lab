"""Read-only OpenAI mock request growth measurements (Issue #115).

Reads OPENAI_MOCK_LOG JSONL without emitting message bodies or secrets.
Only wire_messages is available in the current mock log; these are
role/ID projections, not the full content sent to the model.
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
        result.append({
            "line": line_number,
            "chat_id": chat_id if isinstance(chat_id, str) and chat_id else None,
            "turn_index": per_chat[key],
            "completion_id": row.get("completion_id"),
            "message_count": len(messages),
            "tool_message_count": sum(m.get("role") == "tool" for m in messages),
            "projection_json_chars": len(json.dumps(messages, ensure_ascii=False)),
            "content_chars": None,
            "content_measurement": "unavailable_in_wire_projection",
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
