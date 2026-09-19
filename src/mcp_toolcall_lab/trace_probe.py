"""Harvest and correlate IDs issued across one lab hop.

``chat_sim.send_via_chat(chat_id=...)`` lets you pin the lab-owned conversation
key. Real UIs and model APIs then mint *other* ids we do not own: OpenAI
``chatcmpl-*`` / ``call_*``, Responses/reasoning ``resp_`` / ``rs_`` / ``msg_`` /
``fc_``, LibreChat ``conversationId`` / ``messageId`` in the page URL, Open
WebUI ``chat.id`` / ``share_id``. This module is a **probe**, not a product
database — it only reads surfaces the lab already has (MCP JSONL ``_meta``,
the OpenAI mock log, an observation row, a page URL, leftover text).

Typical use::

    python -m mcp_toolcall_lab.trace_probe kinds
    python -m mcp_toolcall_lab.trace_probe --chat-id chat_abc \\
        --mcp-log test-results/mcp-toolcalls.jsonl \\
        --openai-log test-results/openai-mock.jsonl \\
        --url 'http://127.0.0.1:3080/c/66f012345678901234567890'

Same ID string in two hops joins them (union-find). We do **not** join on
shared user text by default — every Yokohama smoke would collapse into one
blob. Pin a ``chat_id`` (or any other minted id) and the probe returns the
cluster hanging off it.

Not part of ``INLINE_MODULES`` in export.py: harness-side, like ``chat_sim``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from mcp_toolcall_lab.mock.common import read_jsonl

# Prefix / shape table. Longer prefixes first so chatcmpl- wins over chat_.
# owner=lab means we mint it; everything else is harvested only.

ID_KINDS: tuple[dict[str, str], ...] = (
    {
        "kind": "completion_id",
        "owner": "openai-wire",
        "prefix": "chatcmpl-",
        "notes": "POST /v1/chat/completions response id (stream chunks share it).",
    },
    {
        "kind": "response_id",
        "owner": "openai-responses",
        "prefix": "resp_",
        "notes": "Responses API response.id, if a client ever logs one.",
    },
    {
        "kind": "reasoning_id",
        "owner": "openai-responses",
        "prefix": "rs_",
        "notes": "Responses reasoning item id (not chat/completions).",
    },
    {
        "kind": "responses_message_id",
        "owner": "openai-responses",
        "prefix": "msg_",
        "notes": "Responses message item id. Distinct from a UI messageId.",
    },
    {
        "kind": "function_call_item_id",
        "owner": "openai-responses",
        "prefix": "fc_",
        "notes": "Responses function_call item id. Distinct from call_*.",
    },
    {
        "kind": "lab_chat_id",
        "owner": "lab",
        "prefix": "chat_",
        "notes": "chat_sim.new_chat_id() / send --chat-id. Not LibreChat's conversationId.",
    },
    {
        "kind": "call_id",
        "owner": "openai-wire",
        "prefix": "call_",
        "notes": "tool_calls[].id / tool_call_id. chat_sim matches this shape on purpose.",
    },
    {
        "kind": "trace_id",
        "owner": "lab",
        "prefix": "",
        "notes": "Opaque send_direct / MCP _meta.trace_id. No prefix; key-harvested only.",
    },
    {
        "kind": "conversation_id",
        "owner": "chat-ui",
        "prefix": "",
        "notes": "LibreChat /c/{id} or conversationId. OWUI /c/{chat.id}. Harvested from URL/keys.",
    },
    {
        "kind": "ui_message_id",
        "owner": "chat-ui",
        "prefix": "",
        "notes": "LibreChat messageId / parentMessageId; OWUI message id. URL or known keys.",
    },
    {
        "kind": "parent_message_id",
        "owner": "chat-ui",
        "prefix": "",
        "notes": "LibreChat parentMessageId (thread parent).",
    },
    {
        "kind": "share_id",
        "owner": "chat-ui",
        "prefix": "",
        "notes": "OWUI /s/{share_id} (independent uuid, not derived from chat.id).",
    },
)

_PREFIX_KINDS: tuple[tuple[str, str], ...] = tuple(
    (row["prefix"], row["kind"]) for row in ID_KINDS if row["prefix"]
)

# Field names we treat as ids even without a prefix (product uuids / mongo ids).
_KEY_KIND: dict[str, str] = {
    "chat_id": "lab_chat_id",
    "lab_chat_id": "lab_chat_id",
    "call_id": "call_id",
    "call_ids": "call_id",
    "inbound_call_ids": "call_id",
    "tool_call_id": "call_id",
    "trace_id": "trace_id",
    "lab_trace_id": "trace_id",
    "completion_id": "completion_id",
    "response_id": "response_id",
    "reasoning_id": "reasoning_id",
    "conversationid": "conversation_id",
    "conversation_id": "conversation_id",
    "messageid": "ui_message_id",
    "message_id": "ui_message_id",
    "parentmessageid": "parent_message_id",
    "parent_message_id": "parent_message_id",
    "share_id": "share_id",
    "shareid": "share_id",
}

_SKIP_PATHS = frozenset({"new", "c", "s", "login", "register", "auth"})
_PREFIX_RE = re.compile(
    r"(chatcmpl-[A-Za-z0-9]+|resp_[A-Za-z0-9]+|rs_[A-Za-z0-9]+|"
    r"msg_[A-Za-z0-9]+|fc_[A-Za-z0-9]+|chat_[0-9a-fA-F]{16,}|call_[A-Za-z0-9]{8,})"
)


@dataclass(frozen=True)
class FoundId:
    kind: str
    value: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "source": self.source}


@dataclass
class Hop:
    source: str
    at: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    ids: list[FoundId] = field(default_factory=list)


def kinds_catalog() -> list[dict[str, str]]:
    return [dict(row) for row in ID_KINDS]


def classify_value(value: str, *, key: str | None = None) -> str | None:
    """Return an ID kind for ``value``. Prefixes beat field-name hints."""
    text = str(value).strip()
    if not text or len(text) < 4:
        return None
    for prefix, kind in _PREFIX_KINDS:
        if text.startswith(prefix):
            return kind
    if key is None:
        return None
    hint = _KEY_KIND.get(key) or _KEY_KIND.get(key.lower())
    if hint == "lab_chat_id" and not text.startswith("chat_"):
        # Real UIs mint conversation/chat ids that are uuids or mongo ids.
        return "conversation_id"
    return hint


def resume_chat_path(chat_id: str | None) -> str | None:
    """``/c/{id}`` for a product conversation id; None for lab ``chat_*`` tags."""
    if not chat_id:
        return None
    token = chat_id.strip().strip("/")
    if not token or token.lower() in _SKIP_PATHS:
        return None
    if token.startswith("chat_"):
        return None
    if classify_value(token) in {
        "completion_id",
        "response_id",
        "reasoning_id",
        "responses_message_id",
        "function_call_item_id",
        "call_id",
    }:
        return None
    return f"/c/{token}"


def extract_ids_from_url(url: str, *, source: str = "url") -> list[FoundId]:
    if not url:
        return []
    parsed = urlparse(url)
    found: list[FoundId] = []
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "c" and parts[1].lower() not in _SKIP_PATHS:
        found.append(FoundId("conversation_id", parts[1], source))
        if len(parts) >= 3 and parts[2].lower() not in _SKIP_PATHS:
            found.append(FoundId("ui_message_id", parts[2], source))
    if len(parts) >= 2 and parts[0] == "s" and parts[1].lower() not in _SKIP_PATHS:
        found.append(FoundId("share_id", parts[1], source))
    query = parse_qs(parsed.query)
    for raw_key, values in query.items():
        kind = classify_value(values[0], key=raw_key) if values else None
        if kind:
            for value in values:
                if value:
                    found.append(FoundId(kind, value, f"{source}_query"))
    return _dedupe(found)


def extract_ids_from_text(text: str, *, source: str = "text") -> list[FoundId]:
    if not text:
        return []
    found: list[FoundId] = []
    for match in _PREFIX_RE.findall(text):
        kind = classify_value(match)
        if kind:
            found.append(FoundId(kind, match, source))
    return _dedupe(found)


def extract_ids_from_obj(obj: Any, *, source: str, key: str | None = None) -> list[FoundId]:
    found: list[FoundId] = []
    if isinstance(obj, dict):
        for child_key, child in obj.items():
            found.extend(extract_ids_from_obj(child, source=source, key=str(child_key)))
        return _dedupe(found)
    if isinstance(obj, list):
        for child in obj:
            found.extend(extract_ids_from_obj(child, source=source, key=key))
        return _dedupe(found)
    if isinstance(obj, str):
        kind = classify_value(obj, key=key)
        if kind:
            found.append(FoundId(kind, obj.strip(), source))
        else:
            found.extend(extract_ids_from_text(obj, source=source))
    return _dedupe(found)


def _dedupe(ids: Iterable[FoundId]) -> list[FoundId]:
    seen: set[tuple[str, str, str]] = set()
    out: list[FoundId] = []
    for item in ids:
        stamp = (item.kind, item.value, item.source)
        if stamp in seen:
            continue
        seen.add(stamp)
        out.append(item)
    return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def hops_from_mcp_log(path: Path) -> list[Hop]:
    hops: list[Hop] = []
    for event in _read_jsonl(path):
        ids = extract_ids_from_obj(event, source="mcp_log")
        hops.append(
            Hop(
                source="mcp_log",
                at=event.get("at") if isinstance(event.get("at"), str) else None,
                detail={"tool": event.get("tool"), "outcome": event.get("outcome")},
                ids=ids,
            )
        )
    return hops


def hops_from_openai_log(path: Path) -> list[Hop]:
    hops: list[Hop] = []
    for event in _read_jsonl(path):
        hops.append(
            Hop(
                source="openai_log",
                at=event.get("at") if isinstance(event.get("at"), str) else None,
                detail={
                    "kind": event.get("kind"),
                    "user": event.get("user"),
                    "tool_names": event.get("tool_names"),
                },
                ids=extract_ids_from_obj(event, source="openai_log"),
            )
        )
    return hops


def hops_from_observations(path: Path) -> list[Hop]:
    hops: list[Hop] = []
    for event in _read_jsonl(path):
        ids = extract_ids_from_obj(event, source="observe")
        page_url = event.get("page_url") or event.get("url")
        if isinstance(page_url, str):
            ids.extend(extract_ids_from_url(page_url, source="observe_url"))
        hops.append(
            Hop(
                source="observe",
                at=event.get("at") if isinstance(event.get("at"), str) else None,
                detail={
                    "verdict": event.get("verdict"),
                    "antipattern_id": event.get("antipattern_id"),
                    "client": event.get("client"),
                },
                ids=_dedupe(ids),
            )
        )
    return hops


def ids_from_labels(labels: dict[str, str] | None, *, source: str = "flag") -> list[FoundId]:
    if not labels:
        return []
    found: list[FoundId] = []
    for key, value in labels.items():
        if not value:
            continue
        kind = classify_value(value, key=key)
        if kind:
            found.append(FoundId(kind, value, source))
    return _dedupe(found)


def collect_hops(
    *,
    mcp_log: Path | None = None,
    openai_log: Path | None = None,
    observe: Path | None = None,
    page_url: str | None = None,
    text: str | None = None,
    labels: dict[str, str] | None = None,
) -> list[Hop]:
    """Load harvest hops.

    ``labels`` (``--chat-id`` etc.) are *not* hops on their own — that would
    make every probe report a match. They attach onto a URL/text hop so a
    one-liner can pin a lab ``chat_id`` to a product ``/c/{id}`` without
    writing an observation first.
    """
    hops: list[Hop] = []
    if mcp_log is not None:
        hops.extend(hops_from_mcp_log(mcp_log))
    if openai_log is not None:
        hops.extend(hops_from_openai_log(openai_log))
    if observe is not None:
        hops.extend(hops_from_observations(observe))
    surface: list[FoundId] = []
    detail: dict[str, Any] = {}
    if page_url:
        surface.extend(extract_ids_from_url(page_url))
        detail["url"] = page_url
    if text:
        surface.extend(extract_ids_from_text(text))
    attached = ids_from_labels(labels)
    if surface or (attached and page_url):
        hops.append(
            Hop(
                source="query",
                detail=detail,
                ids=_dedupe([*attached, *surface]),
            )
        )
    return hops


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        root_l, root_r = self.find(left), self.find(right)
        if root_l != root_r:
            self.parent[root_r] = root_l


def cluster_hops(hops: list[Hop]) -> list[dict[str, Any]]:
    """Connected components: same id string in two hops joins those hops."""
    uf = _UnionFind()
    for index, hop in enumerate(hops):
        node = f"hop:{index}"
        uf.add(node)
        for found in hop.ids:
            uf.union(node, f"id:{found.value}")
    buckets: dict[str, list[int]] = {}
    for index, hop in enumerate(hops):
        if not hop.ids:
            # Isolated empty hop — skip; it cannot join anything.
            continue
        buckets.setdefault(uf.find(f"hop:{index}"), []).append(index)

    clusters: list[dict[str, Any]] = []
    for hop_indexes in buckets.values():
        ids = _dedupe(found for index in hop_indexes for found in hops[index].ids)
        clusters.append(
            {
                "ids": [item.as_dict() for item in ids],
                "by_kind": _by_kind(ids),
                "hops": [_hop_dict(hops[index]) for index in hop_indexes],
            }
        )
    clusters.sort(key=lambda row: (-len(row["ids"]), row["by_kind"].get("lab_chat_id", [""])[0]))
    return clusters


def _by_kind(ids: Iterable[FoundId]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in ids:
        bucket = grouped.setdefault(item.kind, [])
        if item.value not in bucket:
            bucket.append(item.value)
    return grouped


def _hop_dict(hop: Hop) -> dict[str, Any]:
    return {
        "source": hop.source,
        "at": hop.at,
        "detail": hop.detail,
        "ids": [item.as_dict() for item in hop.ids],
    }


def _cluster_values(cluster: dict[str, Any]) -> set[str]:
    return {item["value"] for item in cluster["ids"]}


def probe(
    hops: list[Hop],
    *,
    needles: dict[str, str] | None = None,
) -> dict[str, Any]:
    clusters = cluster_hops(hops)
    wanted = {key: value for key, value in (needles or {}).items() if value}
    if not wanted:
        return {
            "matched": bool(clusters),
            "match": "all",
            "needles": {},
            "clusters": clusters,
        }
    values = set(wanted.values())
    all_hits = [cluster for cluster in clusters if values <= _cluster_values(cluster)]
    if all_hits:
        return {"matched": True, "match": "all", "needles": wanted, "clusters": all_hits}
    any_hits = [cluster for cluster in clusters if values & _cluster_values(cluster)]
    return {
        "matched": bool(any_hits),
        "match": "any" if any_hits else "none",
        "needles": wanted,
        "clusters": any_hits,
    }


def snapshot_trace(
    *,
    mcp_log: Path | None = None,
    openai_log: Path | None = None,
    page_url: str | None = None,
    text: str | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One-hop harvest for ``chat_ui send`` / observation rows."""
    hops = collect_hops(
        mcp_log=mcp_log,
        openai_log=openai_log,
        page_url=page_url,
        text=text,
        labels=labels,
    )
    ids = _dedupe(
        [
            *ids_from_labels(labels),
            *(found for hop in hops for found in hop.ids),
        ]
    )
    return {
        "ids": [item.as_dict() for item in ids],
        "by_kind": _by_kind(ids),
        "page_url": page_url,
        "labels": {key: value for key, value in (labels or {}).items() if value},
    }


def _default_log(env_name: str, fallback: str) -> str:
    return os.environ.get(env_name, fallback)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mcp_toolcall_lab.trace_probe",
        description="Harvest/correlate chat, call, completion, reasoning, and UI ids.",
    )
    parser.add_argument(
        "cmd",
        nargs="?",
        default="probe",
        choices=("probe", "kinds"),
        help="kinds = ID taxonomy; probe = cluster logs (default)",
    )
    parser.add_argument("--chat-id", default="", help="lab chat_* or a product conversation id")
    parser.add_argument("--call-id", default="")
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--completion-id", default="")
    parser.add_argument("--any-id", default="", help="match this string regardless of kind")
    parser.add_argument("--url", default="", help="LibreChat /c/{id} or OWUI /c/{id} /s/{id}")
    parser.add_argument("--text", default="", help="extra blob to scan for prefixed ids")
    parser.add_argument("--mcp-log", default=_default_log("MCP_TOOLCALL_LOG", "test-results/mcp-toolcalls.jsonl"))
    parser.add_argument("--openai-log", default=_default_log("OPENAI_MOCK_LOG", "test-results/openai-mock.jsonl"))
    parser.add_argument("--observe", default=_default_log("ANTIPATTERN_LOG", "test-results/antipatterns.jsonl"))
    args = parser.parse_args(argv)

    if args.cmd == "kinds":
        print(json.dumps({"kinds": kinds_catalog()}, indent=2, ensure_ascii=False))
        return 0

    hops = collect_hops(
        mcp_log=Path(args.mcp_log),
        openai_log=Path(args.openai_log),
        observe=Path(args.observe),
        page_url=args.url or None,
        text=args.text or None,
        labels={
            "lab_chat_id": args.chat_id,
            "call_id": args.call_id,
            "trace_id": args.trace_id,
            "completion_id": args.completion_id,
        },
    )
    needles = {
        "chat_id": args.chat_id,
        "call_id": args.call_id,
        "trace_id": args.trace_id,
        "completion_id": args.completion_id,
        "any_id": args.any_id,
    }
    report = probe(hops, needles=needles)
    report["resume_path"] = resume_chat_path(args.chat_id or None)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if needles and any(needles.values()) and not report["matched"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
