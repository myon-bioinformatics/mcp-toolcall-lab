"""CPU-class stand-in model: stdlib OpenAI-compatible, no GPU, no extra deps.

Accuracy is not the point. This process exists so compose can start a
``cpu-llm`` service on the same Docker network as the stub UI and MCP mock.

Swap this image for a real tiny GGUF (llama.cpp) behind the same DNS
``http://cpu-llm:8080/v1`` — that overlay is the GPT slice.

Run: ``HOST=0.0.0.0 PORT=8080 python demos/cpu_llm_lite.py``
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

try:
    from mcp_toolcall_lab.mock.common import (
        append_jsonl,
        close_http11_sse,
        new_call_id,
    )
except ImportError:
    from common import append_jsonl, close_http11_sse, new_call_id  # type: ignore[no-redef]

MODEL_ID = os.environ.get("CPU_LLM_MODEL", "lab-cpu-lite")
LOG_PATH = os.environ.get("CPU_LLM_LOG", "")


def _log(event: dict[str, Any]) -> None:
    if LOG_PATH:
        append_jsonl(LOG_PATH, event)


def _models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "mcp-toolcall-lab"}],
    }


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


def decide(body: dict[str, Any]) -> dict[str, Any]:
    names = _tool_names(body)
    messages = list(body.get("messages") or [])
    user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            user = str(message.get("content") or "")
            break
    lowered = user.lower()
    municipality = next((name for name in names if "find_municipalities" in name), None)
    if municipality and ("yokohama" in lowered or "municipalit" in lowered):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": new_call_id(),
                    "type": "function",
                    "function": {
                        "name": municipality,
                        "arguments": json.dumps({"query": "Yokohama"}),
                    },
                }
            ],
        }
    return {
        "role": "assistant",
        "content": (
            f"lab-cpu-lite heard {user!r}. Accuracy is out of scope. "
            "Ask for Yokohama municipalities with tools attached to try MCP."
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path in {"", "/health"}:
            self._send_json({"ok": True, "model": MODEL_ID})
            return
        if path.endswith("/models"):
            self._send_json(_models())
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if not path.endswith("/chat/completions"):
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        message = decide(body)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        _log({"kind": "chat.completions", "model": MODEL_ID, "id": completion_id})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            close_http11_sse(self)
            self.end_headers()
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{"index": 0, "delta": message, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
            return
        self._send_json(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            }
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write("cpu_llm_lite: " + (format % args) + "\n")


def serve(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"cpu-llm-lite http://{host}:{port}/v1 model={MODEL_ID}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    serve(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8080")))
