"""Ultra-light chat stub: markdown corpus + stdlib HTTP, almost no deps.

LibreChat / Open WebUI stay the products under test. This module is a
**reference front** you can run with ``python -m mcp_toolcall_lab.stub_front``:

* ATX headings (``#`` … ``######``) are the deterministic "model". Pass a
  heading, get that section's body back as the assistant turn. Not CommonMark
  — accuracy work against ``myon-bioinformatics/markdown`` is a later slice.
* The same composer locators both products use (``data-testid=text-input`` /
  ``send-button`` and ``#chat-input`` / ``#send-message-button`` /
  ``#response-content-container``) so Playwright can point here without Docker.
* ``/c/{chat_id}`` mints and keeps a lab chat id, then puts it on MCP
  ``_meta`` *and* ``X-Chat-Id`` so the mock log is never an orphan row.
* Prompts that look like the lab tools (Yokohama / stations / prices) take
  the MCP path instead of heading lookup — the case split the real UIs hide.

No FastMCP / Playwright import on the serve path. MCP is optional urllib
JSON-RPC; without ``--mcp`` the catalog is called in-process and still logged.

    python -m mcp_toolcall_lab.stub_front turn --heading "Yokohama"
    python -m mcp_toolcall_lab.stub_front serve --port 8765
    python -m mcp_toolcall_lab.stub_front serve --mcp http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from mcp_toolcall_lab.catalog import (
    search_municipalities,
    search_stations,
    search_transaction_prices,
)
from mcp_toolcall_lab.chat_sim import new_call_id, new_chat_id
from mcp_toolcall_lab.record import (
    OUTCOME_EMPTY,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    record_call,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "fixtures" / "stub_front"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

CASE_HEADING_HIT = "HEADING_HIT"
CASE_HEADING_MISS = "HEADING_MISS"
CASE_MCP_SUCCESS = "MCP_SUCCESS"
CASE_MCP_EMPTY = "MCP_EMPTY"
CASE_MCP_ERROR = "MCP_ERROR"
CASE_MCP_UNREACHABLE = "MCP_UNREACHABLE"

# Dual locators: LibreChat testids + Open WebUI ids (see frontends.py).
LIBRECHAT_INPUT = "text-input"
LIBRECHAT_SEND = "send-button"
OWUI_INPUT = "chat-input"
OWUI_SEND = "send-message-button"
OWUI_RESPONSE = "response-content-container"


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    slug: str
    body: str
    line: int


@dataclass
class Turn:
    chat_id: str
    call_id: str
    case: str
    user: str
    assistant: str
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    mcp_outcome: str | None = None
    heading: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify(title: str) -> str:
    lowered = title.casefold().strip()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def parse_sections(markdown: str) -> list[Section]:
    """Split ATX headings only. Setext / HTML / indented fences are out of scope."""
    lines = markdown.splitlines()
    found: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            found.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[Section] = []
    for idx, (start, level, title) in enumerate(found):
        end = found[idx + 1][0] if idx + 1 < len(found) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        sections.append(Section(level, title, slugify(title), body, start + 1))
    return sections


def load_corpus(root: Path | None = None) -> list[Section]:
    directory = root or DEFAULT_CORPUS
    sections: list[Section] = []
    if not directory.is_dir():
        return sections
    for path in sorted(directory.glob("*.md")):
        sections.extend(parse_sections(path.read_text(encoding="utf-8")))
    return sections


def lookup_heading(query: str, sections: list[Section], *, fuzzy: bool = True) -> Section | None:
    needle = query.strip()
    if needle.startswith("#"):
        needle = needle.lstrip("#").strip()
    if not needle:
        return None
    slug = slugify(needle)
    folded = needle.casefold()
    for section in sections:
        if section.title == needle or section.title.casefold() == folded or section.slug == slug:
            return section
    if not fuzzy:
        return None
    for section in sections:
        title = section.title.casefold()
        if title.startswith(folded) or folded.startswith(title) or folded in title:
            return section
    return None


def _municipality_query(text: str) -> str:
    lowered = text.lower()
    if "yokohama" in lowered or "横浜" in text:
        return "Yokohama"
    if "matsudo" in lowered:
        return "Matsudo"
    if "chiyoda" in lowered or "tokyo" in lowered:
        return "Chiyoda"
    return text.strip()


def classify_prompt(prompt: str, sections: list[Section]) -> dict[str, Any]:
    """Exact heading wins (見出し→本文). Else MCP keywords. ``# Title`` forces heading."""
    text = prompt.strip()
    forced_heading = text.startswith("#")
    exact = lookup_heading(text, sections, fuzzy=False)
    if exact is not None:
        return {"kind": "heading", "section": exact}
    lowered = text.casefold()
    if not forced_heading:
        if any(token in lowered for token in ("station", "駅", "find_stations")):
            if "00000" in text or "unknown" in lowered:
                code = "00000"
            elif "yokohama" in lowered or "横浜" in text:
                code = "14109"
            elif "matsudo" in lowered:
                code = "12207"
            else:
                code = "13101"
            return {"kind": "mcp", "tool": "find_stations", "arguments": {"municipality_code": code}}
        if any(token in lowered for token in ("price", "transaction", "価格", "find_transaction")):
            return {
                "kind": "mcp",
                "tool": "find_transaction_prices",
                "arguments": {"municipality_code": "14109", "year": 2025},
            }
        if any(token in lowered for token in ("yokohama", "横浜", "municipalit", "市区町村", "find_municipalities")):
            return {
                "kind": "mcp",
                "tool": "find_municipalities",
                "arguments": {"query": _municipality_query(text)},
            }
    section = lookup_heading(text, sections, fuzzy=True)
    if section:
        return {"kind": "heading", "section": section}
    return {"kind": "miss"}


def render_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_empty list (valid call, no rows)_"
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(str(key))
    header = "| " + " | ".join(keys) + " |"
    sep = "| " + " | ".join("---" for _ in keys) + " |"
    body = ["| " + " | ".join(str(row.get(key, "")) for key in keys) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _inprocess_tool(tool: str, arguments: dict[str, Any]) -> tuple[str, Any]:
    if tool == "find_municipalities":
        result = search_municipalities(str(arguments.get("query", "")))
    elif tool == "find_stations":
        result = search_stations(str(arguments.get("municipality_code", "")))
    elif tool == "find_transaction_prices":
        result = search_transaction_prices(
            str(arguments.get("municipality_code", "")),
            int(arguments.get("year", 2025)),
        )
    else:
        return OUTCOME_ERROR, f"unknown tool {tool}"
    return (OUTCOME_EMPTY if result == [] else OUTCOME_SUCCESS), result


class McpStdlibSession:
    """Streamable HTTP client with urllib only — same handshake as the curl tests."""

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 1

    def _post(self, payload: dict[str, Any], extra_headers: dict[str, str] | None = None) -> str:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if extra_headers:
            headers.update(extra_headers)
        request = Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            session = response.headers.get("mcp-session-id")
            if session:
                self.session_id = session
            return response.read().decode("utf-8")

    def initialize(self) -> None:
        raw = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "stub-front", "version": "0.0.1"},
                },
            }
        )
        self._next_id += 1
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        _extract_sse_data(raw)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        chat_id: str,
    ) -> dict[str, Any]:
        raw = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments, "_meta": meta},
            },
            extra_headers={"X-Chat-Id": chat_id},
        )
        self._next_id += 1
        return _extract_sse_data(raw)


def _extract_sse_data(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no SSE/JSON body: {text[:200]!r}") from exc


def _call_mcp(
    *,
    tool: str,
    arguments: dict[str, Any],
    chat_id: str,
    call_id: str,
    mcp_url: str | None,
) -> tuple[str, Any, str]:
    meta = {"chat_id": chat_id, "call_id": call_id, "source": "stub-front"}
    if not mcp_url:
        outcome, payload = _inprocess_tool(tool, arguments)
        record_call(tool=tool, arguments=arguments, outcome=outcome, result=payload, meta=meta)
        return outcome, payload, "inprocess"
    try:
        session = McpStdlibSession(mcp_url)
        session.initialize()
        body = session.call_tool(tool, arguments, meta=meta, chat_id=chat_id)
        result = body.get("result") or body
        if result.get("isError"):
            return OUTCOME_ERROR, result, "mcp"
        structured = result.get("structuredContent") or {}
        payload = structured.get("result", structured)
        if payload == [] or payload == {}:
            return OUTCOME_EMPTY, payload, "mcp"
        return OUTCOME_SUCCESS, payload, "mcp"
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return OUTCOME_ERROR, str(exc), "mcp"


def reply(
    prompt: str,
    *,
    chat_id: str | None = None,
    sections: list[Section] | None = None,
    mcp_url: str | None = None,
    corpus: Path | None = None,
) -> Turn:
    chat_id = chat_id or new_chat_id()
    call_id = new_call_id()
    sections = sections if sections is not None else load_corpus(corpus)
    classified = classify_prompt(prompt, sections)
    if classified["kind"] == "heading":
        section: Section = classified["section"]
        return Turn(
            chat_id=chat_id,
            call_id=call_id,
            case=CASE_HEADING_HIT,
            user=prompt,
            assistant=section.body or f"_(no body under {section.title})_",
            heading=section.title,
            notes=f"ATX heading L{section.line}",
        )
    if classified["kind"] == "miss":
        titles = ", ".join(section.title for section in sections[:12]) or "(empty corpus)"
        return Turn(
            chat_id=chat_id,
            call_id=call_id,
            case=CASE_HEADING_MISS,
            user=prompt,
            assistant=f"No heading matched. Known: {titles}",
            notes="heading lookup miss",
        )

    tool = str(classified["tool"])
    arguments = dict(classified["arguments"])
    outcome, payload, via = _call_mcp(
        tool=tool, arguments=arguments, chat_id=chat_id, call_id=call_id, mcp_url=mcp_url
    )
    if via == "mcp" and outcome == OUTCOME_ERROR and isinstance(payload, str):
        case = CASE_MCP_UNREACHABLE if "urlopen" in payload or "timed out" in payload else CASE_MCP_ERROR
        assistant = f"MCP {case}: {payload}"
    elif outcome == OUTCOME_ERROR:
        case = CASE_MCP_ERROR
        assistant = f"MCP error: {payload}"
    elif outcome == OUTCOME_EMPTY:
        case = CASE_MCP_EMPTY
        assistant = render_rows([])
    else:
        case = CASE_MCP_SUCCESS
        assistant = render_rows(payload) if isinstance(payload, list) else json.dumps(payload, ensure_ascii=False)
    return Turn(
        chat_id=chat_id,
        call_id=call_id,
        case=case,
        user=prompt,
        assistant=assistant,
        tool=tool,
        arguments=arguments,
        mcp_outcome=outcome,
        notes=via,
    )


def _page(chat_id: str, turns: list[Turn], prompt: str = "") -> str:
    bubbles = []
    for turn in turns:
        bubbles.append(
            f'<section class="turn" data-case="{html.escape(turn.case)}">'
            f"<h3>user</h3><pre>{html.escape(turn.user)}</pre>"
            f"<h3>assistant · {html.escape(turn.case)}</h3>"
            f'<article id="{OWUI_RESPONSE}"><pre>{html.escape(turn.assistant)}</pre></article>'
            f"</section>"
        )
    thread = "\n".join(bubbles) or "<p>Send a heading (e.g. <code>Find municipalities</code>) or <code>Yokohama</code>.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>stub-front {html.escape(chat_id)}</title>
<style>
 body {{ font-family: sans-serif; max-width: 52rem; margin: 1.5rem auto; }}
 textarea {{ width: 100%; min-height: 4rem; }}
</style></head>
<body>
<p>lab chat_id <code data-testid="chat-id">{html.escape(chat_id)}</code> · stdlib stub</p>
<form method="post" action="/c/{html.escape(chat_id)}">
<textarea name="prompt" data-testid="{LIBRECHAT_INPUT}" id="{OWUI_INPUT}">{html.escape(prompt)}</textarea>
<button type="submit" data-testid="{LIBRECHAT_SEND}" id="{OWUI_SEND}">Send</button>
</form>
{thread}
</body></html>
"""


@dataclass
class StubState:
    sections: list[Section]
    mcp_url: str | None
    chats: dict[str, list[Turn]] = field(default_factory=dict)


def make_handler(state: StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _chat_id_from_path(self) -> str | None:
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            if len(parts) >= 2 and parts[0] == "c" and parts[1] not in {"new"}:
                return parts[1]
            return None

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/health":
                self._send(200, b'{"ok":true}\n', "application/json")
                return
            if path in {"/", "/login", "/c", "/c/new"}:
                chat_id = new_chat_id()
                self.send_response(302)
                self.send_header("Location", f"/c/{chat_id}")
                self.end_headers()
                return
            chat_id = self._chat_id_from_path()
            if chat_id:
                state.chats.setdefault(chat_id, [])
                page = _page(chat_id, state.chats[chat_id])
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(404, b"not found\n", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            path = urlparse(self.path).path
            prompt = ""
            if self.headers.get("Content-Type", "").startswith("application/json"):
                try:
                    payload = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    self._send(400, b'{"error":"invalid json"}\n', "application/json")
                    return
                prompt = str(payload.get("prompt") or payload.get("heading") or "")
                chat_id = str(payload.get("chat_id") or self._chat_id_from_path() or new_chat_id())
            else:
                form = parse_qs(raw.decode("utf-8"))
                prompt = (form.get("prompt") or [""])[0]
                chat_id = self._chat_id_from_path() or new_chat_id()
            turn = reply(prompt, chat_id=chat_id, sections=state.sections, mcp_url=state.mcp_url)
            state.chats.setdefault(chat_id, []).append(turn)
            if path.startswith("/api/"):
                self._send(
                    200,
                    (json.dumps(turn.as_dict(), ensure_ascii=False) + "\n").encode("utf-8"),
                    "application/json",
                )
                return
            self._send(200, _page(chat_id, state.chats[chat_id]).encode("utf-8"), "text/html; charset=utf-8")

    return Handler


def serve(host: str, port: int, *, corpus: Path, mcp_url: str | None) -> None:
    state = StubState(sections=load_corpus(corpus), mcp_url=mcp_url)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"stub-front http://{host}:{port}/  corpus={corpus} mcp={mcp_url or 'inprocess'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mcp_toolcall_lab.stub_front")
    sub = parser.add_subparsers(dest="cmd")
    turn = sub.add_parser("turn", help="one heading/MCP turn as JSON (no server)")
    turn.add_argument("--heading", "--prompt", dest="prompt", default="", required=True)
    turn.add_argument("--chat-id", default="")
    turn.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    turn.add_argument("--mcp", default=os.environ.get("STUB_MCP_URL", ""))
    serve_p = sub.add_parser("serve", help="stdlib HTTP composer")
    serve_p.add_argument("--host", default=os.environ.get("STUB_HOST", "127.0.0.1"))
    serve_p.add_argument("--port", type=int, default=int(os.environ.get("STUB_PORT", "8765")))
    serve_p.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    serve_p.add_argument("--mcp", default=os.environ.get("STUB_MCP_URL", ""))
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 2
    if args.cmd == "turn":
        result = reply(
            args.prompt,
            chat_id=args.chat_id or None,
            corpus=Path(args.corpus),
            mcp_url=args.mcp or None,
        )
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return 0 if result.case != CASE_HEADING_MISS else 1
    serve(args.host, args.port, corpus=Path(args.corpus), mcp_url=args.mcp or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
