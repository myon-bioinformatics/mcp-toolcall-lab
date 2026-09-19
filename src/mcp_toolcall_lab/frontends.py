"""Chat-UI contracts for Playwright: LibreChat and Open WebUI.

This module is the **source of truth** for “where do I click?” and “what
Python names mean?”. Tests, Docker smoke, and ``python -m mcp_toolcall_lab.chat_ui``
all import these objects instead of hard-coding selectors.

Nothing here imports Playwright. Reading this file is enough to drive a
browser, write a one-liner, or teach another agent the frontends.

Provenance was checked against upstream source (not guessed):

* LibreChat ``danny-avila/LibreChat`` — ``data-testid="text-input"`` /
  ``send-button`` in ``client/src/components/Chat/Input/ChatForm.tsx`` and
  ``SendButton.tsx``; login ``data-testid="login-button"`` /
  field testids in ``Auth/LoginForm.tsx`` and ``Registration.tsx``;
  MCP tool keys ``{tool}_mcp_{server}`` in
  ``packages/data-provider/src/config.ts`` (``mcp_delimiter``).
* Open WebUI ``open-webui/open-webui`` — ``#chat-input`` /
  ``#send-message-button`` in ``MessageInput.svelte``; ``#response-content-container``
  in ``ResponseMessage.svelte``; ``GET /health`` and ``WEBUI_AUTH=False``
  auto-signin in ``backend/open_webui/main.py`` / ``routers/auths.py``.

Dump the catalog (no browser)::

    python -m mcp_toolcall_lab.frontends
    python -m mcp_toolcall_lab.frontends librechat
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS

ClientId = Literal["librechat", "openwebui"]
LocatorKind = Literal["css", "testid", "id", "label", "role"]

LIBRECHAT_MCP_DELIMITER = "_mcp_"
SAMPLE_PROMPT = "Find municipalities named Yokohama"
SAMPLE_RESULT_FRAGMENT = "Yokohama"


@dataclass(frozen=True)
class Locator:
    """One way to find a control. ``css()`` is what Playwright ``locator()`` wants."""

    kind: LocatorKind
    value: str
    fallbacks: tuple[str, ...] = ()
    provenance: str = ""
    notes: str = ""

    def css(self) -> str:
        if self.kind == "testid":
            return f'[data-testid="{self.value}"]'
        if self.kind == "id":
            return f"#{self.value}"
        if self.kind == "css":
            return self.value
        if self.kind == "label":
            return f'[aria-label*="{self.value}" i]'
        if self.kind == "role":
            return f'[role="{self.value}"]'
        raise ValueError(self.kind)

    def all_css(self) -> str:
        parts = [self.css(), *self.fallbacks]
        return ", ".join(parts)


@dataclass(frozen=True)
class AuthContract:
    """How a first-time Playwright run reaches the composer."""

    mode: Literal["form", "webuiauth_off"]
    login_path: str
    register_api: str | None
    register_fields: tuple[str, ...]
    email: Locator | None
    password: Locator | None
    submit: Locator | None
    notes: str = ""


@dataclass(frozen=True)
class ComposerContract:
    """The only UI surface the smoke *must* finish: type + Send."""

    input: Locator
    send: Locator
    enter_sends: bool
    mcp_picker: Locator | None
    default_mcp_server: str | None
    notes: str = ""


@dataclass(frozen=True)
class ResponseContract:
    """Where the assistant reply shows up after Send."""

    container: Locator | None
    ready_substrings: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class McpWireContract:
    """How this UI names MCP tools on the OpenAI wire."""

    compose_service: str
    compose_mcp_url: str
    compose_openai_url: str | None
    tool_key_style: Literal["bare", "name_mcp_server"]
    default_server: str
    notes: str = ""

    def tool_key(self, tool_name: str, server: str | None = None) -> str:
        server = server or self.default_server
        if self.tool_key_style == "bare":
            return tool_name
        return f"{tool_name}{LIBRECHAT_MCP_DELIMITER}{server}"


@dataclass(frozen=True)
class ChatFrontend:
    """One product's Playwright + Docker contract."""

    id: ClientId
    product: str
    default_url: str
    health_path: str
    compose_file: str | None
    auth: AuthContract
    composer: ComposerContract
    response: ResponseContract
    mcp: McpWireContract
    sample_prompt: str = SAMPLE_PROMPT
    sample_result_fragment: str = SAMPLE_RESULT_FRAGMENT
    advertised_tools: tuple[str, ...] = AVAILABLE_TOOLS
    one_liners: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LIBRECHAT = ChatFrontend(
    id="librechat",
    product="LibreChat (danny-avila/LibreChat)",
    default_url="http://127.0.0.1:3080",
    health_path="/",
    compose_file="docker/librechat-smoke/docker-compose.yml",
    auth=AuthContract(
        mode="form",
        login_path="/login",
        register_api="/api/auth/register",
        register_fields=("name", "username", "email", "password", "confirm_password"),
        email=Locator(
            "testid",
            "email",
            fallbacks=('input[name="email"]', 'input[type="email"]'),
            provenance="client/src/components/Auth/LoginForm.tsx — data-testid={id}",
        ),
        password=Locator(
            "testid",
            "password",
            fallbacks=('input[name="password"]', 'input[type="password"]'),
            provenance="client/src/components/Auth/LoginForm.tsx",
        ),
        submit=Locator(
            "testid",
            "login-button",
            provenance="client/src/components/Auth/LoginForm.tsx data-testid=login-button",
        ),
        notes=(
            "Smoke env sets ALLOW_REGISTRATION / ALLOW_EMAIL_LOGIN / "
            "ALLOW_UNVERIFIED_EMAIL_LOGIN. POST register then fill login."
        ),
    ),
    composer=ComposerContract(
        input=Locator(
            "testid",
            "text-input",
            provenance="client/src/components/Chat/Input/ChatForm.tsx data-testid=text-input",
            notes="Composer textarea (not contenteditable).",
        ),
        send=Locator(
            "testid",
            "send-button",
            provenance="client/src/components/Chat/Input/SendButton.tsx data-testid=send-button type=submit",
        ),
        enter_sends=True,
        mcp_picker=Locator(
            "label",
            "MCP",
            fallbacks=('button[aria-label*="MCP" i]',),
            provenance="client/src/components/Chat/Input/MCPSelect.tsx aria-label com_ui_mcp_servers",
            notes="chatMenu: true on mcpServers.lab. Click, then the row named `lab`.",
        ),
        default_mcp_server="lab",
        notes="Hard contract for CI: find text-input, type, click send-button.",
    ),
    response=ResponseContract(
        container=None,
        ready_substrings=(SAMPLE_RESULT_FRAGMENT, "No MCP tools", "Kanagawa", "error"),
        notes=(
            "LibreChat has no single response id like OWUI. Wait on body text "
            "signals, then classify via MCP_TOOLCALL_LOG."
        ),
    ),
    mcp=McpWireContract(
        compose_service="librechat",
        compose_mcp_url="http://mcp-mock:8000/mcp",
        compose_openai_url="http://openai-mock:8090/v1",
        tool_key_style="name_mcp_server",
        default_server="lab",
        notes=(
            "OpenAI tools[].function.name is find_municipalities_mcp_lab when "
            "the yaml key is `lab`. openai-mock matches names containing "
            "find_municipalities."
        ),
    ),
    one_liners=(
        "python -m mcp_toolcall_lab.frontends librechat",
        "python -m mcp_toolcall_lab.chat_ui describe librechat",
        "python -m mcp_toolcall_lab.chat_ui send --client librechat --url http://127.0.0.1:3080",
        "python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1 --url http://127.0.0.1:3080/c/CONVERSATION",
    ),
)

OPENWEBUI = ChatFrontend(
    id="openwebui",
    product="Open WebUI (open-webui/open-webui)",
    default_url="http://127.0.0.1:3000",
    health_path="/health",
    compose_file=None,
    auth=AuthContract(
        mode="webuiauth_off",
        login_path="/",
        register_api=None,
        register_fields=(),
        email=None,
        password=None,
        submit=None,
        notes=(
            "WEBUI_AUTH=False: /auth calls signInHandler() and creates "
            "admin@localhost / admin. Wait for #chat-input; no login form."
        ),
    ),
    composer=ComposerContract(
        input=Locator(
            "id",
            "chat-input",
            provenance="src/lib/components/chat/MessageInput.svelte id=chat-input",
            notes="contenteditable / rich text. Prefer click + keyboard.type, not fill().",
        ),
        send=Locator(
            "id",
            "send-message-button",
            provenance="src/lib/components/chat/MessageInput.svelte id=send-message-button",
            notes="Enter without Shift also submits (unless ctrlEnterToSend).",
        ),
        enter_sends=True,
        mcp_picker=None,
        default_mcp_server=None,
        notes="Admin UI: add MCP (Streamable HTTP) connection, not OpenAPI.",
    ),
    response=ResponseContract(
        container=Locator(
            "id",
            "response-content-container",
            provenance="src/lib/components/chat/Messages/ResponseMessage.svelte",
            notes="Use .last — each assistant message has this id.",
        ),
        ready_substrings=(SAMPLE_RESULT_FRAGMENT, "Kanagawa"),
        notes="Assert table/pre/li inside the last response-content-container.",
    ),
    mcp=McpWireContract(
        compose_service="open-webui",
        compose_mcp_url="http://mcp-mock:8000/mcp",
        compose_openai_url=None,
        tool_key_style="bare",
        default_server="",
        notes="Native Streamable HTTP MCP. Tool names stay find_municipalities (no _mcp_ suffix).",
    ),
    one_liners=(
        "python -m mcp_toolcall_lab.frontends openwebui",
        "python -m mcp_toolcall_lab.chat_ui describe openwebui",
        "python -m mcp_toolcall_lab.chat_ui send --client openwebui --url http://127.0.0.1:3000",
        "python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1 --url http://127.0.0.1:3000/c/CHAT",
    ),
)

FRONTENDS: dict[ClientId, ChatFrontend] = {
    "librechat": LIBRECHAT,
    "openwebui": OPENWEBUI,
}


def get_frontend(client: str) -> ChatFrontend:
    key = client.strip().lower().replace("_", "").replace("-", "")
    aliases = {"owui": "openwebui", "openwebui": "openwebui", "librechat": "librechat", "lc": "librechat"}
    resolved = aliases.get(key)
    if resolved is None:
        known = ", ".join(FRONTENDS)
        raise SystemExit(f"unknown client {client!r}; expected one of: {known}")
    return FRONTENDS[resolved]


def catalog() -> dict[str, Any]:
    """JSON-serializable map for agents / one-liners / fixtures."""
    return {
        "sample_prompt": SAMPLE_PROMPT,
        "sample_result_fragment": SAMPLE_RESULT_FRAGMENT,
        "mcp_delimiter_librechat": LIBRECHAT_MCP_DELIMITER,
        "advertised_tools": list(AVAILABLE_TOOLS),
        "clients": {name: frontend.to_dict() for name, frontend in FRONTENDS.items()},
        "one_liners": {
            "dump": "python -m mcp_toolcall_lab.frontends",
            "describe": "python -m mcp_toolcall_lab.chat_ui describe",
            "send_librechat": LIBRECHAT.one_liners[-2],
            "send_openwebui": OPENWEBUI.one_liners[-2],
            "trace_probe": "python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1",
            "trace_kinds": "python -m mcp_toolcall_lab.trace_probe kinds",
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    payload: Any
    if args and args[0] not in {"-h", "--help"}:
        payload = get_frontend(args[0]).to_dict()
    else:
        if args and args[0] in {"-h", "--help"}:
            print(__doc__)
            print("usage: python -m mcp_toolcall_lab.frontends [librechat|openwebui]")
            return 0
        payload = catalog()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
