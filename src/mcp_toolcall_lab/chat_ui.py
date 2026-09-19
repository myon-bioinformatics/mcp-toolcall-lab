"""Drive LibreChat / Open WebUI from the shared frontend catalog.

Playwright is imported only for ``send``. ``describe`` is stdlib + this package::

    python -m mcp_toolcall_lab.chat_ui describe
    python -m mcp_toolcall_lab.chat_ui describe librechat
    python -m mcp_toolcall_lab.chat_ui send --client librechat --url http://127.0.0.1:3080
    python -m mcp_toolcall_lab.chat_ui send --client openwebui --url http://127.0.0.1:3000 --prompt "Find municipalities named Yokohama"

``send`` prints one JSON object (flags + optional anti-pattern classification
+ a ``trace`` harvest of chat/call/completion/UI ids). Pin a lab key with
``--chat-id``; product conversation ids resume ``/c/{id}`` instead of
starting a new thread.

    python -m mcp_toolcall_lab.chat_ui send --client librechat --chat-id chat_lab1
    python -m mcp_toolcall_lab.trace_probe --chat-id chat_lab1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.antipatterns import (
    classify_observation,
    load_mcp_log,
    openai_request_had_tools,
    write_observation,
)
from mcp_toolcall_lab.frontends import (
    SAMPLE_PROMPT,
    ChatFrontend,
    FRONTENDS,
    catalog,
    get_frontend,
)
from mcp_toolcall_lab.trace_probe import resume_chat_path, snapshot_trace


def describe(client: str | None = None) -> dict[str, Any]:
    if client:
        frontend = get_frontend(client)
        return {
            "client": frontend.id,
            "composer_input": frontend.composer.input.css(),
            "composer_send": frontend.composer.send.css(),
            "default_url": frontend.default_url,
            "compose_mcp_url": frontend.mcp.compose_mcp_url,
            "tool_key_example": frontend.mcp.tool_key("find_municipalities"),
            "one_liners": list(frontend.one_liners),
            "sample_prompt": frontend.sample_prompt,
        }
    return catalog()


def _register(frontend: ChatFrontend, base_url: str, email: str, password: str, name: str) -> None:
    if not frontend.auth.register_api:
        return
    try:
        import httpx
    except ImportError:
        return
    payload = {
        "name": name,
        "username": email.split("@")[0],
        "email": email,
        "password": password,
        "confirm_password": password,
    }
    try:
        httpx.post(f"{base_url}{frontend.auth.register_api}", json=payload, timeout=15)
    except Exception:
        return


def login(
    page: Any,
    frontend: ChatFrontend,
    base_url: str,
    email: str,
    password: str,
    resume_path: str | None = None,
) -> bool:
    """Reach the composer. Returns False if the input locator never appears."""
    input_css = frontend.composer.input.css()
    target = f"{base_url}{resume_path}" if resume_path else base_url
    if frontend.auth.mode == "webuiauth_off":
        page.goto(target, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(input_css, timeout=45_000)
            return True
        except Exception:
            return False

    assert frontend.auth.email and frontend.auth.password
    page.goto(f"{base_url}{frontend.auth.login_path}", wait_until="domcontentloaded")
    try:
        page.locator(frontend.auth.email.all_css()).first.wait_for(timeout=20_000)
        page.locator(frontend.auth.email.all_css()).first.fill(email)
        page.locator(frontend.auth.password.all_css()).first.fill(password)
        if frontend.auth.submit:
            button = page.locator(frontend.auth.submit.css())
            if button.count():
                button.click()
            else:
                page.keyboard.press("Enter")
        else:
            page.keyboard.press("Enter")
        page.wait_for_selector(input_css, timeout=45_000)
        if resume_path:
            page.goto(target, wait_until="domcontentloaded")
            page.wait_for_selector(input_css, timeout=20_000)
        return True
    except Exception:
        try:
            page.goto(target, wait_until="domcontentloaded")
            page.wait_for_selector(input_css, timeout=20_000)
            return True
        except Exception:
            return False


def enable_mcp_picker(page: Any, frontend: ChatFrontend) -> None:
    picker = frontend.composer.mcp_picker
    server = frontend.composer.default_mcp_server
    if picker is None:
        return
    trigger = page.locator(picker.all_css())
    if trigger.count() == 0:
        return
    try:
        trigger.first.click(timeout=5_000)
        if server:
            row = page.get_by_text(server, exact=True)
            if row.count():
                row.first.click(timeout=5_000)
        page.keyboard.press("Escape")
    except Exception:
        return


def type_and_send(page: Any, frontend: ChatFrontend, text: str) -> dict[str, bool]:
    flags = {"input_found": False, "send_clicked": False}
    box = page.locator(frontend.composer.input.css())
    box.wait_for(timeout=15_000)
    flags["input_found"] = True
    box.click()
    page.keyboard.type(text, delay=15)
    send = page.locator(frontend.composer.send.css())
    send.wait_for(timeout=10_000)
    send.click()
    flags["send_clicked"] = True
    return flags


def wait_for_assistant(page: Any, frontend: ChatFrontend, timeout_ms: int = 45_000) -> bool:
    if frontend.response.container is not None:
        try:
            page.locator(frontend.response.container.css()).last.wait_for(timeout=timeout_ms)
            return True
        except Exception:
            return False
    quoted = [json.dumps(s) for s in frontend.response.ready_substrings]
    js = "() => { const t = document.body ? document.body.innerText : ''; return " + " || ".join(
        f"t.includes({q})" for q in quoted
    ) + "; }"
    try:
        page.wait_for_function(js, timeout=timeout_ms)
        return True
    except Exception:
        return False


def send_in_browser(
    *,
    frontend: ChatFrontend,
    base_url: str,
    prompt: str,
    email: str,
    password: str,
    name: str = "Lab Smoke",
    headed: bool = False,
    resume_chat_id: str | None = None,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    _register(frontend, base_url, email, password, name)
    flags = {"input_found": False, "send_clicked": False}
    logged_in = False
    assistant_visible = False
    ui_text = ""
    page_url = ""
    resume = resume_chat_path(resume_chat_id)
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed, executable_path=executable)
        page = browser.new_page()
        try:
            logged_in = login(page, frontend, base_url, email, password, resume_path=resume)
            if logged_in:
                enable_mcp_picker(page, frontend)
                flags = type_and_send(page, frontend, prompt)
                assistant_visible = wait_for_assistant(page, frontend)
                ui_text = page.locator("body").inner_text()
                page_url = page.url
        finally:
            browser.close()
    return {
        "client": frontend.id,
        "logged_in": logged_in,
        **flags,
        "assistant_visible": assistant_visible,
        "page_url": page_url,
        "resume_path": resume,
        "ui_text_len": len(ui_text),
        "ui_has_fragment": frontend.sample_result_fragment.lower() in ui_text.lower(),
        "ui_text_tail": ui_text[-1500:],
    }


def classify_send_result(
    *,
    frontend: ChatFrontend,
    flags: dict[str, Any],
    mcp_log: Path,
    openai_log: Path,
) -> dict[str, Any]:
    return classify_observation(
        input_found=bool(flags.get("input_found")),
        send_clicked=bool(flags.get("send_clicked")),
        logged_in=bool(flags.get("logged_in")),
        assistant_visible=bool(flags.get("assistant_visible")),
        openai_saw_tools=openai_request_had_tools(openai_log),
        mcp_calls=load_mcp_log(mcp_log),
        ui_text=str(flags.get("ui_text_tail") or ""),
        expected_ui_fragment=frontend.sample_result_fragment,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mcp_toolcall_lab.chat_ui",
        description="Describe or drive LibreChat / Open WebUI using shared frontend defs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    desc = sub.add_parser("describe", help="print selectors / one-liners (no browser)")
    desc.add_argument("client", nargs="?", choices=sorted(FRONTENDS), help="omit for full catalog")

    send = sub.add_parser("send", help="Playwright: type + click Send (needs the UI up)")
    send.add_argument("--client", default="librechat", choices=sorted(FRONTENDS))
    send.add_argument("--url", default="", help="default: frontend.default_url")
    send.add_argument("--prompt", default=SAMPLE_PROMPT)
    send.add_argument("--email", default="")
    send.add_argument("--password", default=os.environ.get("LIBRECHAT_SMOKE_PASSWORD", "LabSmoke123!"))
    send.add_argument("--headed", action="store_true")
    send.add_argument(
        "--chat-id",
        default=os.environ.get("LAB_CHAT_ID", ""),
        help="lab chat_* tag, or a product conversation id (resumes /c/{id})",
    )
    send.add_argument("--call-id", default=os.environ.get("LAB_CALL_ID", ""))
    send.add_argument("--trace-id", default=os.environ.get("LAB_TRACE_ID", ""))
    send.add_argument("--mcp-log", default=os.environ.get("MCP_TOOLCALL_LOG", "test-results/mcp-toolcalls.jsonl"))
    send.add_argument("--openai-log", default=os.environ.get("OPENAI_MOCK_LOG", "test-results/openai-mock.jsonl"))
    send.add_argument("--observe", default=os.environ.get("ANTIPATTERN_LOG", "test-results/antipatterns.jsonl"))

    args = parser.parse_args(argv)
    if args.cmd == "describe":
        print(json.dumps(describe(args.client), indent=2, ensure_ascii=False))
        return 0

    frontend = get_frontend(args.client)
    base = (args.url or os.environ.get("LIBRECHAT_BASE_URL") or os.environ.get("OPEN_WEBUI_BASE_URL") or frontend.default_url).rstrip("/")
    email = args.email or os.environ.get("LIBRECHAT_SMOKE_EMAIL") or f"smoke-{uuid.uuid4().hex[:8]}@lab.example"
    try:
        flags = send_in_browser(
            frontend=frontend,
            base_url=base,
            prompt=args.prompt,
            email=email,
            password=args.password,
            headed=args.headed,
            resume_chat_id=args.chat_id or None,
        )
    except ModuleNotFoundError:
        print("playwright is not installed. pip install -e '.[browser-test]' && playwright install chromium", file=sys.stderr)
        return 2
    classified = classify_send_result(
        frontend=frontend,
        flags=flags,
        mcp_log=Path(args.mcp_log),
        openai_log=Path(args.openai_log),
    )
    labels = {
        "lab_chat_id": args.chat_id,
        "call_id": args.call_id,
        "trace_id": args.trace_id,
    }
    trace = snapshot_trace(
        mcp_log=Path(args.mcp_log),
        openai_log=Path(args.openai_log),
        page_url=flags.get("page_url") or None,
        text=str(flags.get("ui_text_tail") or ""),
        labels=labels,
    )
    record = write_observation(
        Path(args.observe),
        observation={
            **classified,
            **flags,
            "prompt": args.prompt,
            "url": base,
            "lab_chat_id": args.chat_id or None,
            "lab_call_id": args.call_id or None,
            "lab_trace_id": args.trace_id or None,
            "trace": trace,
        },
        source=f"{frontend.id}-chat-ui",
    )
    print(json.dumps(record, indent=2, ensure_ascii=False))
    # Input+Send failure is the only hard error; MCP miss is recorded.
    if not flags.get("send_clicked"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
