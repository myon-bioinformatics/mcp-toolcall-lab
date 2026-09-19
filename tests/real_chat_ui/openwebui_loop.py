"""Read OpenAI + MCP JSONL as the products' own wire — no extra schema.

The OpenAI mock already logs ``POST /v1/chat/completions`` projections
(``tool_names``, ``call_ids``, ``inbound_call_ids``, ``wire_messages``).
The MCP mock logs JSON-RPC method names as ``event`` (``initialize``,
``tools/list``, ``tools/call``). This module only joins those rows.
"""

from __future__ import annotations

from typing import Any

from mcp_toolcall_lab.record import EVENT_INITIALIZE, EVENT_TOOLS_CALL, EVENT_TOOLS_LIST, mcp_tool_calls
from mcp_toolcall_lab.trace_probe import classify_value, extract_ids_from_url, snapshot_trace


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
        "message_ids": mcp_header_message_ids(events),
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


_LAB_SHAPED = (
    "lab_chat_id",
    "call_id",
    "completion_id",
    "response_id",
    "reasoning_id",
    "responses_message_id",
    "function_call_item_id",
)


def is_product_ui_message_id(value: str | None) -> bool:
    """True for an OWUI uuid-like message id. Empty / templates / lab prefixes fail."""
    text = str(value or "").strip()
    if len(text) < 8:
        return False
    if "{{" in text or "}}" in text or "MESSAGE_ID" in text:
        return False
    kind = classify_value(text)
    return kind not in _LAB_SHAPED


def mcp_header_message_ids(events: list[dict[str, Any]]) -> list[str]:
    """``debug.message_id`` from ``X-OpenWebUI-Message-Id`` (not minted)."""
    found: list[str] = []
    for event in events:
        debug = event.get("debug") if isinstance(event.get("debug"), dict) else {}
        value = debug.get("message_id") or event.get("message_id")
        if not value:
            continue
        text = str(value)
        if text not in found:
            found.append(text)
    return found


def mcp_tools_call_message_ids(events: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for event in mcp_tool_calls(events):
        debug = event.get("debug") if isinstance(event.get("debug"), dict) else {}
        value = debug.get("message_id") or event.get("message_id")
        if not value:
            continue
        text = str(value)
        if text not in found:
            found.append(text)
    return found


def message_ids_from_owui_chat(payload: Any) -> list[str]:
    """Walk Open WebUI chat JSON for product message ids (any role)."""
    ids: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)

    def walk_messages(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        messages = obj.get("messages")
        if isinstance(messages, dict):
            for key, msg in messages.items():
                add(key)
                if isinstance(msg, dict) and msg.get("id"):
                    add(msg.get("id"))
        elif isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("id"):
                    add(msg.get("id"))
        for nested in (obj.get("history"), obj.get("chat")):
            if isinstance(nested, dict):
                walk_messages(nested)
        current = obj.get("current_message_id") or obj.get("currentId")
        if current:
            add(current)

    if isinstance(payload, list):
        for item in payload:
            walk_messages(item)
    else:
        walk_messages(payload)
    return ids


def harvest_openwebui_user_message_ids(page: Any) -> list[str]:
    """Product message ids from ``GET /api/v1/chats/{id}`` in the browser origin."""
    try:
        payload = page.evaluate(
            """async () => {
                const tryFetch = async (url) => {
                    const res = await fetch(url, { credentials: 'same-origin' });
                    if (!res.ok) return { ok: false, status: res.status, body: null };
                    return { ok: true, status: res.status, body: await res.json() };
                };
                const listHit = await tryFetch('/api/v1/chats/');
                const list = listHit.ok && Array.isArray(listHit.body) ? listHit.body : [];
                const chats = [];
                for (const item of list.slice(0, 20)) {
                    const id = item && item.id ? item.id : item;
                    if (!id) continue;
                    const hit = await tryFetch('/api/v1/chats/' + id);
                    if (hit.ok) chats.push(hit.body);
                }
                const dom = [];
                for (const el of document.querySelectorAll('[id^="message-index-input-"]')) {
                    dom.push(el.id.slice('message-index-input-'.length));
                }
                return { list_status: listHit.status, chats, dom };
            }"""
        )
    except Exception:
        return []
    ids: list[str] = []
    if isinstance(payload, dict):
        for chat in payload.get("chats") or []:
            for item in message_ids_from_owui_chat(chat):
                if item not in ids:
                    ids.append(item)
        for item in payload.get("dom") or []:
            text = str(item).strip()
            if text and text not in ids:
                ids.append(text)
    return ids


def attach_openwebui_chat_api_collector(page: Any) -> list[str]:
    """Collect user message ids from in-flight ``/api/v1/chats`` responses."""
    captured: list[str] = []

    def _on_response(response: Any) -> None:
        try:
            url = str(response.url or "")
            if "/api/v1/chats" not in url or int(response.status) >= 400:
                return
            data = response.json()
        except Exception:
            return
        for item in message_ids_from_owui_chat(data):
            if item not in captured:
                captured.append(item)

    page.on("response", _on_response)
    return captured


def assert_openwebui_tool_loop(
    *,
    openai_events: list[dict[str, Any]],
    mcp_events: list[dict[str, Any]],
    page_url: str,
    ui_text: str,
    expected_fragment: str = "Yokohama",
    mcp_tool: str = "find_municipalities",
    ui_message_ids: list[str] | None = None,
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
    header_message_ids = mcp_tools_call_message_ids(mcp_events)
    harvested_ui_ids = [str(item) for item in (ui_message_ids or []) if str(item).strip()]

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

    assert header_message_ids, (
        "Open WebUI did not send X-OpenWebUI-Message-Id on tools/call — "
        "cannot PASS without a product UI message id"
    )
    for message_id in header_message_ids:
        assert is_product_ui_message_id(message_id), (
            f"tools/call message_id is not a product UI id: {message_id!r}"
        )
    assert harvested_ui_ids, (
        "could not harvest Open WebUI user message id from GET /api/v1/chats "
        "or the send-hop chat API — cannot PASS"
    )
    assert any(is_product_ui_message_id(item) for item in harvested_ui_ids), harvested_ui_ids
    assert header_message_ids[-1] in harvested_ui_ids, (
        header_message_ids[-1],
        harvested_ui_ids,
    )

    assert expected_fragment.lower() in ui_text.lower(), ui_text[-500:]

    return {
        "openai": openai_loop,
        "mcp": mcp_loop,
        "conversation_id": conversation_id,
        "ui_message_id": header_message_ids[-1],
        "ui_message_ids": harvested_ui_ids,
        "ids": ids,
        "trace": trace,
    }
