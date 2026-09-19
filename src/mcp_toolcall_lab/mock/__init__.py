"""Shared stdlib helpers for the lab's mocks.

MCP ``record`` / FastMCP ``server`` and ``demos/openai_toolcall_mock.py``
stay separate. They only share JSONL I/O, id mints, well-known chat
headers, and the HTTP/1.1 SSE close. Protocol logic and log schemas
do not live here — force-joining those would couple two wire formats.
"""

from .common import (
    CHAT_ID_HEADER_KEYS,
    MESSAGE_ID_HEADER_KEYS,
    append_jsonl,
    chat_id_from_headers,
    close_http11_sse,
    id_from_headers,
    new_call_id,
    new_chat_id,
    read_jsonl,
)

__all__ = [
    "CHAT_ID_HEADER_KEYS",
    "MESSAGE_ID_HEADER_KEYS",
    "append_jsonl",
    "chat_id_from_headers",
    "close_http11_sse",
    "id_from_headers",
    "new_call_id",
    "new_chat_id",
    "read_jsonl",
]
