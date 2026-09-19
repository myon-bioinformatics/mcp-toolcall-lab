"""Read OpenAI + MCP JSONL as the products' own wire — no extra schema.

The OpenAI mock already logs ``POST /v1/chat/completions`` projections
(``tool_names``, ``call_ids``, ``inbound_call_ids``, ``wire_messages``).
The MCP mock logs JSON-RPC method names as ``event`` (``initialize``,
``tools/list``, ``tools/call``). This module only joins those rows.
"""

from __future__ import annotations

from typing import Any

from mcp_toolcall_lab.record import EVENT_INITIALIZE, EVENT_TOOLS_CALL, EVENT_TOOLS_LIST, mcp_tool_calls
from mcp_toolcall_lab.trace_probe import extract_ids_from_url, snapshot_trace


def openai_completions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("kind") == "chat.completions"]


def is_background_task(event: dict[str, Any]) -> bool:
    """Open WebUI title/tags/follow-up jobs. Not the chat turn under test."""
    return str(event.get("user") or "").lstrip().startswith("### Task:")


def summarize_openai_tool_loop(events: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [event for event in openai_completions(events) if not is_background_task(event)]
    first: dict[str, Any] = {}
    for row in rows:
        if row.get("call_ids") and row.get("tool_names"):
            first = row
    follow: dict[str, Any] = {}
    call_ids = [str(item) for item in (first.get("call_ids") or [])]
    seen_first = not first
    for row in rows:
        if first and row.get("completion_id") == first.get("completion_id"):
            seen_first = True
            continue
        if not seen_first:
            continue
        inbound = [str(item) for item in (row.get("inbound_call_ids") or [])]
        if row.get("has_tool_result") and call_ids and set(call_ids) <= set(inbound):
            follow = row
            break
    inbound = [str(item) for item in (follow.get("inbound_call_ids") or [])]
    follow_tool_ids = []
    for message in follow.get("wire_messages") or []:
        if message.get("role") == "tool" and message.get("tool_call_id"):
            follow_tool_ids.append(str(message["tool_call_id"]))
    loop_rows = [row for row in (first, follow) if row]
    return {
        "completion_n": len(loop_rows),
        "first_completion_id": first.get("completion_id"),
        "follow_completion_id": follow.get("completion_id"),
        "tool_names": list(first.get("tool_names") or []),
        "call_ids": call_ids,
        "follow_inbound_call_ids": inbound,
        "follow_tool_message_ids": follow_tool_ids,
        "has_tool_result_followup": bool(follow),
        "final_is_assistant": (follow.get("wire_assistant") or {}).get("role") == "assistant"
        and not (follow.get("wire_assistant") or {}).get("tool_calls"),
        "chat_id": first.get("chat_id") or follow.get("chat_id"),
    }


def summarize_mcp_handshake(events: list[dict[str, Any]]) -> dict[str, Any]:
    methods = [str(event.get("event") or "") for event in events]
    calls = mcp_tool_calls(events)
    session_ids = []
    request_ids = []
    for event in events:
        debug = event.get("debug") if isinstance(event.get("debug"), dict) else {}
        session = debug.get("session_id")
        request = debug.get("request_id")
        if session and session not in session_ids:
            session_ids.append(session)
        if request and request not in request_ids:
            request_ids.append(str(request))
    return {
        "methods": methods,
        "initialize": EVENT_INITIALIZE in methods,
        "tools_list": EVENT_TOOLS_LIST in methods,
        "tools_call": any(event.get("event", EVENT_TOOLS_CALL) == EVENT_TOOLS_CALL and event.get("tool") for event in events),
        "tool_names": [str(event.get("tool")) for event in calls],
        "session_ids": session_ids,
        "request_ids": request_ids,
    }


def product_conversation_id(
    page_url: str | None,
    events: list[dict[str, Any]] | None = None,
) -> str | None:
    if page_url:
        for found in extract_ids_from_url(page_url):
            if found.kind == "conversation_id":
                return found.value
    if events:
        product = lab_vs_product_chat_ids(events)["product_chat_ids"]
        if product:
            return product[-1]
    return None


def lab_vs_product_chat_ids(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Split MCP/OpenAI log chat_id values into lab ``chat_*`` vs product ids."""
    lab: list[str] = []
    product: list[str] = []
    for event in events:
        value = event.get("chat_id")
        if not value:
            debug = event.get("debug") if isinstance(event.get("debug"), dict) else {}
            value = debug.get("chat_id")
        if not value:
            continue
        text = str(value)
        bucket = lab if text.startswith("chat_") else product
        if text not in bucket:
            bucket.append(text)
    return {"lab_chat_ids": lab, "product_chat_ids": product}


def assert_openwebui_tool_loop(
    *,
    openai_events: list[dict[str, Any]],
    mcp_events: list[dict[str, Any]],
    page_url: str,
    ui_text: str,
    expected_fragment: str = "Yokohama",
    mcp_tool: str = "find_municipalities",
) -> dict[str, Any]:
    """Hard-assert UI → OpenAI tool_calls → MCP → tool follow-up → UI."""
    openai_loop = summarize_openai_tool_loop(openai_events)
    mcp_loop = summarize_mcp_handshake(mcp_events)
    ids = lab_vs_product_chat_ids([*openai_events, *mcp_events])
    conversation_id = product_conversation_id(page_url, [*openai_events, *mcp_events])
    if openai_loop.get("chat_id") and not str(openai_loop["chat_id"]).startswith("chat_"):
        conversation_id = conversation_id or str(openai_loop["chat_id"])
        if conversation_id != openai_loop["chat_id"]:
            # Prefer the id on the tool-call hop over an earlier page URL miss.
            conversation_id = str(openai_loop["chat_id"])
    trace = snapshot_trace(
        page_url=page_url,
        text=ui_text,
        openai_log=None,
        mcp_log=None,
    )

    assert openai_loop["completion_n"] >= 2, openai_loop
    assert any(mcp_tool in name for name in openai_loop["tool_names"]), openai_loop["tool_names"]
    assert openai_loop["call_ids"], "assistant tool_calls[] missing call_id"
    assert all(item.startswith("call_") for item in openai_loop["call_ids"]), openai_loop["call_ids"]
    assert openai_loop["first_completion_id"] and str(openai_loop["first_completion_id"]).startswith(
        "chatcmpl-"
    ), openai_loop["first_completion_id"]
    assert openai_loop["has_tool_result_followup"], "no role:tool follow-up completion"
    assert openai_loop["follow_inbound_call_ids"] == openai_loop["call_ids"] or set(
        openai_loop["call_ids"]
    ) <= set(openai_loop["follow_inbound_call_ids"])
    assert set(openai_loop["call_ids"]) <= set(openai_loop["follow_tool_message_ids"]) or set(
        openai_loop["call_ids"]
    ) <= set(openai_loop["follow_inbound_call_ids"])
    assert openai_loop["final_is_assistant"]
    assert openai_loop["follow_completion_id"] and str(openai_loop["follow_completion_id"]).startswith(
        "chatcmpl-"
    )

    assert mcp_loop["initialize"], mcp_loop["methods"]
    assert mcp_loop["tools_list"], mcp_loop["methods"]
    assert mcp_loop["tools_call"], mcp_loop["methods"]
    assert mcp_tool in mcp_loop["tool_names"], mcp_loop["tool_names"]
    assert mcp_loop["session_ids"], "MCP session_id missing"
    assert mcp_loop["request_ids"], "MCP request_id missing"

    assert conversation_id, f"no product chat.id in {page_url!r} or logs"
    assert not conversation_id.startswith("chat_"), conversation_id
    if ids["product_chat_ids"]:
        assert conversation_id in ids["product_chat_ids"], (conversation_id, ids)
    for lab_id in ids["lab_chat_ids"]:
        assert lab_id != conversation_id
        assert lab_id.startswith("chat_")

    assert expected_fragment.lower() in ui_text.lower(), ui_text[-500:]

    return {
        "openai": openai_loop,
        "mcp": mcp_loop,
        "conversation_id": conversation_id,
        "ids": ids,
        "trace": trace,
    }
