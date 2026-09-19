"""Deterministic OpenAI-compatible chat-completions mock, for the chat-e2e lane only.

Open WebUI and LibreChat both need *some* model backend before "assistant
response can be awaited" (issue #14's chat-e2e minimum bar) means anything —
without one, pressing Send has nothing to answer with. Standing up a real LLM
would make the lane slow, non-deterministic, and dependent on API keys /
GPUs; this mock returns one fixed reply instead, deterministically, using
only the standard library so it needs no extra dependency and no image
beyond the interpreter this repo already targets.

This is chat-e2e test harness, not part of the MCP server under test — it is
not wired into ``export.py``'s ``INLINE_MODULES`` and does not speak MCP at
all, only the OpenAI-style ``/v1/chat/completions`` HTTP shape both chat
products already know how to call as a "custom" model provider.

Tool-calling from this mock (the model deciding to call an MCP tool, per the
issue's staged extension of this lane) is deliberately out of scope here —
see docs/e2e_foundation.md for why that is left as follow-up work.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIXED_REPLY = os.environ.get("MOCK_LLM_REPLY", "This is a deterministic mock assistant response.")


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming convention
        if self.path.startswith("/v1/models"):
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [{"id": "mock-llm", "object": "model", "owned_by": "mcp-toolcall-lab"}],
                },
            )
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming convention
        if not self.path.startswith("/v1/chat/completions"):
            self._send_json(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)  # request body (messages, tools, ...) is unused; the reply is fixed
        self._send_json(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "mock-llm",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": FIXED_REPLY},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep container logs quiet; nothing here is worth logging per request


def run() -> None:
    host = os.environ.get("MOCK_LLM_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCK_LLM_PORT", "8081"))
    server = ThreadingHTTPServer((host, port), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    run()
