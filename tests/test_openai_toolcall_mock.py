from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp_toolcall_lab.mock.common import close_http11_sse, new_call_id

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
if str(DEMOS) not in sys.path:
    sys.path.insert(0, str(DEMOS))

import openai_toolcall_mock as mock  # noqa: E402


def test_no_tools_is_plain_text_for_picker_off() -> None:
    message = mock.decide_assistant_message(
        {"messages": [{"role": "user", "content": "Find municipalities named Yokohama"}]}
    )
    assert message.get("tool_calls") is None
    assert "No MCP tools were attached" in message["content"]


def test_librechat_prefixed_tool_name_emits_tool_call() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "find_municipalities_mcp_lab",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    message = mock.decide_assistant_message(body)
    assert message["tool_calls"][0]["function"]["name"] == "find_municipalities_mcp_lab"
    assert "Yokohama" in message["tool_calls"][0]["function"]["arguments"]


def test_tool_result_turn_returns_yokohama() -> None:
    message = mock.decide_assistant_message(
        {
            "messages": [
                {"role": "user", "content": "Find municipalities named Yokohama"},
                {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
                {"role": "tool", "tool_call_id": "call_1", "content": '[{"name":"Yokohama"}]'},
            ]
        }
    )
    assert "Yokohama" in message["content"]
    assert message.get("tool_calls") is None


def test_completion_and_call_ids_are_prefixed() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
        "tools": [{"type": "function", "function": {"name": "find_municipalities_mcp_lab"}}],
    }
    message = mock.decide_assistant_message(body)
    completion = mock._completion(message, completion_id="chatcmpl-fixed")
    assert completion["id"] == "chatcmpl-fixed"
    call_ids = mock.tool_call_ids_from_message(message)
    assert len(call_ids) == 1
    assert call_ids[0].startswith("call_")
    inbound = mock.inbound_call_ids_from_messages(
        [
            {"role": "assistant", "tool_calls": [{"id": "call_inbound"}]},
            {"role": "tool", "tool_call_id": "call_inbound"},
        ]
    )
    assert inbound == ["call_inbound"]


def test_openwebui_prefixed_tool_name_emits_tool_call() -> None:
    body = {
        "messages": [{"role": "user", "content": "Find municipalities named Yokohama"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lab_find_municipalities",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    message = mock.decide_assistant_message(body)
    assert message["tool_calls"][0]["function"]["name"] == "lab_find_municipalities"
    projected = mock.wire_messages_projection(
        [
            {"role": "user", "content": "Find municipalities named Yokohama"},
            message,
            {"role": "tool", "tool_call_id": message["tool_calls"][0]["id"], "content": "[]"},
        ]
    )
    assert projected[0]["role"] == "user"
    assert projected[1]["tool_calls"][0]["id"].startswith("call_")
    assert projected[2]["tool_call_id"] == message["tool_calls"][0]["id"]


def test_stream_branch_closes_http11_connection() -> None:
    """Keep-alive SSE never ends; LibreChat would hang on the next reuse."""
    source = Path(mock.__file__).read_text(encoding="utf-8")
    assert "close_http11_sse" in source
    assert "MESSAGE_ID_HEADER_KEYS" in source
    common = (ROOT / "src" / "mcp_toolcall_lab" / "mock" / "common.py").read_text(
        encoding="utf-8"
    )
    assert 'send_header("Connection", "close")' in common
    assert "close_connection = True" in common
    assert mock.close_http11_sse is close_http11_sse


def test_openai_mock_uses_shared_call_id_mint() -> None:
    assert mock.new_call_id is new_call_id


def test_openai_log_copies_chat_id_header(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "nested" / "openai.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    mock._log({"kind": "chat.completions"}, headers={"X-Chat-Id": "chat_from_ui"})
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["kind"] == "chat.completions"
    assert row["chat_id"] == "chat_from_ui"


def test_openai_log_copies_message_id_header(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "openai.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    mock._log(
        {"kind": "chat.completions"},
        headers={"X-OpenWebUI-Message-Id": "owui-msg-row"},
    )
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["message_id"] == "owui-msg-row"


def test_content_metrics_strings_parts_empty_and_invalid():
    result = mock.message_content_metrics([
        {"role": "user", "content": "秘密abc"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"},
                                         {"type": "input_text", "text": "日本"}]},
        {"role": "tool", "content": "TOOL_SECRET"},
        {"role": "system", "content": None},
    ])
    assert result["content_chars"] == 5 + 4 + 11
    assert result["role_content_chars"]["assistant"] == 4
    assert result["role_content_chars"]["system"] == 0
    assert result["tool_content_chars"] == 11
    assert "秘密" not in json.dumps(result, ensure_ascii=False)
    assert "TOOL_SECRET" not in json.dumps(result)
    assert mock.message_content_metrics([{"role": "user", "content": []}])["content_chars"] == 0
    for bad in ([None], [{"role": "user", "content": {"text": "private"}}],
                [{"role": "unknown", "content": "secret"}],
                [{"role": "tool", "content": [{"type": "image_url", "image_url": "secret"}]}]):
        assert mock.message_content_metrics(bad) == {"content_measurement": "invalid_messages"}


def test_logged_measurement_never_includes_message_bodies(tmp_path, monkeypatch):
    log = tmp_path / "metrics.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    secret = "UNIQUE_PRIVATE_TOOL_RESULT"
    messages = [{"role": "user", "content": "PRIVATE_PROMPT"},
                {"role": "tool", "content": secret}]
    mock._log({"kind": "chat.completions", "completion_id": "chatcmpl-test",
               "wire_messages": mock.wire_messages_projection(messages),
               **mock.message_content_metrics(messages)})
    raw = log.read_text(encoding="utf-8")
    assert "PRIVATE_PROMPT" not in raw and secret not in raw
    record = json.loads(raw)
    assert "user" not in record
    assert record["content_chars"] == len("PRIVATE_PROMPT") + len(secret)
    assert "chat_id" not in record


def _history_with_arguments(arguments, content=None):
    return [{"role": "assistant", "content": content, "tool_calls": [
        {"id": "call_one", "type": "function", "function": {
            "name": "lookup", "arguments": arguments}},
        {"id": "call_two", "type": "function", "function": {
            "name": "lookup", "arguments": "日本🙂"}},
    ]}, {"role": "tool", "tool_call_id": "call_one", "content": "SECRET_RESULT"}]


def test_full_message_metrics_count_arguments_unicode_and_extra_fields():
    for content in (None, ""):
        messages = _history_with_arguments("a" * 10, content)
        messages[0]["extra"] = "秘密🙂"
        before = json.dumps(messages, ensure_ascii=False)
        small = mock.message_content_metrics(messages)
        messages[0]["tool_calls"][0]["function"]["arguments"] = "a" * 1000
        large = mock.message_content_metrics(messages)
        assert large["message_json_chars"] - small["message_json_chars"] == 990
        assert large["tool_call_argument_chars"] - small["tool_call_argument_chars"] == 990
        assert small["tool_call_argument_chars"] == 13
        assert small["content_chars"] == large["content_chars"] == len("SECRET_RESULT")
        assert small["role_content_chars"]["assistant"] == 0
        assert small["message_json_format"] == "unicode_sorted_compact_v1"
        restored = json.loads(before)
        assert small["message_json_chars"] == len(json.dumps(
            restored, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
        reordered = [dict(reversed(list(m.items()))) for m in restored]
        assert mock.message_content_metrics(reordered) == small
        assert "秘密" not in json.dumps(small, ensure_ascii=False)
    assert mock.message_content_metrics([])["message_json_chars"] == 2
    # Even non-JSON arguments remain opaque strings and are measured, not executed.
    assert mock.message_content_metrics(_history_with_arguments("{broken"))["tool_call_argument_chars"] == 10


def test_invalid_tool_call_structures_fail_closed_without_payload():
    for bad_calls in (None, {}, "SECRET", [None], [{"type": "function", "id": "c"}],
                      [{"type": "function", "id": "c", "function": "SECRET"}],
                      [{"type": "custom", "id": "c", "function": {}}]):
        assert mock.message_content_metrics([{"role": "assistant", "tool_calls": bad_calls}]) == {
            "content_measurement": "invalid_messages"}
    for arguments in (None, {}, [], 17, True):
        assert mock.message_content_metrics(_history_with_arguments(arguments)) == {
            "content_measurement": "invalid_messages"}
    messages = _history_with_arguments("SECRET")
    messages[0]["role"] = "user"
    assert mock.message_content_metrics(messages) == {"content_measurement": "invalid_messages"}
    messages = _history_with_arguments("SECRET")
    messages[0]["extra"] = float("nan")
    assert mock.message_content_metrics(messages) == {"content_measurement": "invalid_messages"}


def test_http_argument_only_growth_is_logged_without_secrets(tmp_path, monkeypatch):
    import threading
    import urllib.request
    import urllib.error
    from http.server import ThreadingHTTPServer
    from mcp_toolcall_lab.history_growth import measure

    log = tmp_path / "ingress.jsonl"
    monkeypatch.setattr(mock, "LOG_PATH", str(log))
    server = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    completions = []
    try:
        for size in (10, 1000):
            messages = _history_with_arguments("SECRET_ARGUMENT" + "a" * size)
            messages.insert(0, {"role": "user", "content": "SECRET_PROMPT"})
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                data=json.dumps({"messages": messages}).encode(),
                headers={"Content-Type": "application/json", "X-Chat-Id": "chat-A",
                         "Authorization": "Bearer SECRET_TOKEN"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                completions.append(json.load(response)["id"])
        request.data = json.dumps({"messages": _history_with_arguments({"secret": "SECRET_BAD"})}).encode()
        try:
            urllib.request.urlopen(request, timeout=5)
            assert False, "malformed arguments accepted"
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert "SECRET_BAD" not in error.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    raw = log.read_text(encoding="utf-8")
    assert all(secret not in raw for secret in (
        "SECRET_ARGUMENT", "SECRET_RESULT", "SECRET_PROMPT", "SECRET_TOKEN", "SECRET_BAD"))
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 2
    small, large = measure(rows)
    assert [r["completion_id"] for r in rows] == completions
    assert [(r["chat_id"], r["turn_index"]) for r in (small, large)] == [("chat-A", 1), ("chat-A", 2)]
    assert large["message_json_chars"] - small["message_json_chars"] == 990
    assert large["tool_call_argument_chars"] - small["tool_call_argument_chars"] == 990
    assert large["projection_json_chars"] == small["projection_json_chars"]
    assert large["content_chars"] == small["content_chars"]
