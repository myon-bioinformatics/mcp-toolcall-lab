"""Stdlib OpenAI-compatible mock that *will* call MCP-shaped tools.

LibreChat (and Open WebUI) talk to a model over ``GET /v1/models`` and
``POST /v1/chat/completions``. This server does not call a real LLM. When the
request includes tools whose name contains ``find_municipalities`` and the user
asked about Yokohama / municipalities, it returns an OpenAI ``tool_calls``
payload so the chat UI can execute the MCP tool. A follow-up request that
already has ``role: tool`` gets a final assistant message containing the
mock result.

If the request has **no** tools, it replies in plain text saying so — that is
how the Playwright job distinguishes MCP_PICKER_OFF from MCP_NOT_CALLED.

Request bodies are appended to ``OPENAI_MOCK_LOG`` (JSONL) when set. Each
row includes ``completion_id`` (``chatcmpl-*``) and ``call_ids`` /
``inbound_call_ids`` so ``trace_probe`` can join them to a lab ``chat_id``.

Run: ``HOST=0.0.0.0 PORT=8090 python demos/openai_toolcall_mock.py``
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MODEL_ID = os.environ.get("OPENAI_MOCK_MODEL", "lab-model")
LOG_PATH = os.environ.get("OPENAI_MOCK_LOG", "")


def _log(event: dict[str, Any]) -> None:
    if not LOG_PATH:
        return
    path = Path(LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _models_payload() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "created": 0,
                "owned_by": "mcp-toolcall-lab",
            }
        ],
    }


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return str(content)
    return ""


def _tool_names(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or tool
        name = function.get("name") if isinstance(function, dict) else None
        if name:
            names.append(str(name))
    return names


def _municipality_tool(tool_names: list[str]) -> str | None:
    for name in tool_names:
        if "find_municipalities" in name:
            return name
    return None


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    return any(message.get("role") == "tool" for message in messages)


def _user_wants_municipality(text: str) -> bool:
    lowered = text.lower()
    return "yokohama" in lowered or "municipalit" in lowered or "市区町村" in lowered


def tool_call_ids_from_message(message: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for tool in message.get("tool_calls") or []:
        if isinstance(tool, dict) and tool.get("id"):
            ids.append(str(tool["id"]))
    return ids


def inbound_call_ids_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        candidates = list(tool_call_ids_from_message(message))
        if message.get("role") == "tool" and message.get("tool_call_id"):
            candidates.append(str(message["tool_call_id"]))
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            ids.append(item)
    return ids


def _tool_call_message(tool_name: str, query: str = "Yokohama") -> dict[str, Any]:
    call_id = f"call_{uuid.uuid4().hex[:24]}"
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps({"query": query}, ensure_ascii=False),
                },
            }
        ],
    }


def _final_message() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": (
            "Yokohama (code 14109) is in Kanagawa. "
            "This came from the mcp-toolcall-lab mock tool find_municipalities."
        ),
    }


def _no_tools_message() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": (
            "No MCP tools were attached to this turn "
            "(LibreChat chat picker / mcpServers not applied)."
        ),
    }


def decide_assistant_message(body: dict[str, Any]) -> dict[str, Any]:
    messages = list(body.get("messages") or [])
    names = _tool_names(body)
    user_text = _last_user_text(messages)
    if _has_tool_result(messages):
        return _final_message()
    tool = _municipality_tool(names)
    if tool and _user_wants_municipality(user_text):
        return _tool_call_message(tool)
    if not names:
        return _no_tools_message()
    return {
        "role": "assistant",
        "content": (
            "Tools were advertised but this prompt does not map to a lab mock tool. "
            f"Ask for Yokohama municipalities. tools={names}"
        ),
    }


def _completion(message: dict[str, Any], completion_id: str | None = None) -> dict[str, Any]:
    finish = "tool_calls" if message.get("tool_calls") else "stop"
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {"index": 0, "message": message, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream_chunks(message: dict[str, Any], chunk_id: str | None = None) -> list[str]:
    chunk_id = chunk_id or f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish_reason: str | None) -> str:
        payload = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL_ID,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    out: list[str] = []
    if message.get("tool_calls"):
        tool = message["tool_calls"][0]
        out.append(
            chunk(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": tool["id"],
                            "type": "function",
                            "function": {"name": tool["function"]["name"], "arguments": ""},
                        }
                    ],
                },
                None,
            )
        )
        out.append(
            chunk(
                {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": tool["function"]["arguments"]}}
                    ]
                },
                None,
            )
        )
        out.append(chunk({}, "tool_calls"))
    else:
        out.append(chunk({"role": "assistant", "content": message.get("content") or ""}, None))
        out.append(chunk({}, "stop"))
    out.append("data: [DONE]\n\n")
    return out


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path.endswith("/models"):
            self._send_json(_models_payload())
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if not path.endswith("/chat/completions"):
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, status=400)
            return

        names = _tool_names(body)
        messages = list(body.get("messages") or [])
        message = decide_assistant_message(body)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        _log(
            {
                "kind": "chat.completions",
                "stream": bool(body.get("stream")),
                "tool_names": names,
                "user": _last_user_text(messages),
                "has_tool_result": _has_tool_result(messages),
                "completion_id": completion_id,
                "call_ids": tool_call_ids_from_message(message),
                "inbound_call_ids": inbound_call_ids_from_messages(messages),
            }
        )
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()
            self.close_connection = True
            for piece in _stream_chunks(message, chunk_id=completion_id):
                self.wfile.write(piece.encode("utf-8"))
                self.wfile.flush()
            return
        self._send_json(_completion(message, completion_id=completion_id))

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write("openai_toolcall_mock: " + (format % args) + "\n")


def serve(host: str = "127.0.0.1", port: int = 8090) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"openai_toolcall_mock listening on http://{host}:{port}/v1", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    serve(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8090")),
    )
