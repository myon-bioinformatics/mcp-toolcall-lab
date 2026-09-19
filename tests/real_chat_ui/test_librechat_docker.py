"""Real LibreChat in Docker: type, click Send, then record whether MCP returned.

Default ``pytest`` skips this module unless ``LIBRECHAT_BASE_URL`` is set
(the ``librechat-docker-smoke`` workflow, or a manual compose run).

The first test *must* finish chat input + Send. The second classifies the
MCP round trip: PASS stays a pass; a miss is written to
``test-results/antipatterns.jsonl`` so we can accumulate anti-patterns
instead of pretending the product always wired tools/call.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
import pytest

from mcp_toolcall_lab.antipatterns import (
    VERDICT_PASS,
    classify_observation,
    load_mcp_log,
    openai_request_had_tools,
    write_observation,
)

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

LIBRECHAT_BASE_URL = os.environ.get("LIBRECHAT_BASE_URL", "").rstrip("/")
MCP_LOG = Path(os.environ.get("MCP_TOOLCALL_LOG", "test-results/mcp-toolcalls.jsonl"))
OPENAI_LOG = Path(os.environ.get("OPENAI_MOCK_LOG", "test-results/openai-mock.jsonl"))
OBSERVED = Path(os.environ.get("ANTIPATTERN_LOG", "test-results/antipatterns.jsonl"))
CHAT_MESSAGE = os.environ.get(
    "LIBRECHAT_CHAT_MESSAGE",
    "Find municipalities named Yokohama",
)

pytestmark = [
    pytest.mark.real_chat_ui,
    pytest.mark.skipif(
        not LIBRECHAT_BASE_URL,
        reason="LIBRECHAT_BASE_URL not set — only docker/librechat-smoke or "
        "the librechat-docker-smoke workflow sets this",
    ),
]

EMAIL = os.environ.get("LIBRECHAT_SMOKE_EMAIL", f"smoke-{uuid.uuid4().hex[:8]}@lab.example")
PASSWORD = os.environ.get("LIBRECHAT_SMOKE_PASSWORD", "LabSmoke123!")
NAME = "Lab Smoke"


def _register() -> None:
    """Best-effort user create; 409/400 after the first run is fine."""
    payload = {
        "name": NAME,
        "username": EMAIL.split("@")[0],
        "email": EMAIL,
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    try:
        httpx.post(f"{LIBRECHAT_BASE_URL}/api/auth/register", json=payload, timeout=15)
    except httpx.HTTPError:
        pass


@pytest.fixture(scope="module")
def browser():
    executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(executable_path=executable_path)
        yield launched
        launched.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    try:
        yield pg
    finally:
        pg.close()
        context.close()


def _login(page) -> bool:
    page.goto(f"{LIBRECHAT_BASE_URL}/login", wait_until="domcontentloaded")
    email = page.locator('[data-testid="email"], input[name="email"], input[type="email"]').first
    password = page.locator(
        '[data-testid="password"], input[name="password"], input[type="password"]'
    ).first
    try:
        email.wait_for(timeout=20_000)
        email.fill(EMAIL)
        password.fill(PASSWORD)
        login = page.locator('[data-testid="login-button"]')
        if login.count():
            login.click()
        else:
            page.keyboard.press("Enter")
        page.wait_for_selector('[data-testid="text-input"]', timeout=45_000)
        return True
    except Exception:
        # Already logged in / landing already on chat.
        try:
            page.goto(LIBRECHAT_BASE_URL, wait_until="domcontentloaded")
            page.wait_for_selector('[data-testid="text-input"]', timeout=20_000)
            return True
        except Exception:
            return False


def _enable_mcp_picker(page) -> None:
    """Open the MCP servers control and turn on `lab` if the menu exists."""
    trigger = page.get_by_label("MCP", exact=False)
    if trigger.count() == 0:
        trigger = page.locator('button[aria-label*="MCP" i]')
    if trigger.count() == 0:
        return
    try:
        trigger.first.click(timeout=5_000)
        row = page.get_by_text("lab", exact=True)
        if row.count():
            row.first.click(timeout=5_000)
        page.keyboard.press("Escape")
    except Exception:
        return


def _type_and_send(page, text: str) -> dict[str, bool]:
    flags = {"input_found": False, "send_clicked": False}
    box = page.locator('[data-testid="text-input"]')
    box.wait_for(timeout=15_000)
    flags["input_found"] = True
    box.click()
    page.keyboard.type(text, delay=15)
    send = page.locator('[data-testid="send-button"]')
    send.wait_for(timeout=10_000)
    send.click()
    flags["send_clicked"] = True
    return flags


def test_librechat_types_message_and_clicks_send(page) -> None:
    """Finish the UI contract: chat input + Send. MCP is the next test."""
    _register()
    assert _login(page), "LibreChat login/register never reached the composer"
    _enable_mcp_picker(page)
    flags = _type_and_send(page, CHAT_MESSAGE)
    assert flags["input_found"]
    assert flags["send_clicked"]


def test_mcp_return_or_record_antipattern(page) -> None:
    """Do not fail the suite just to hide a miss — write the anti-pattern."""
    _register()
    logged_in = _login(page)
    flags = {"input_found": False, "send_clicked": False}
    assistant_visible = False
    ui_text = ""
    if logged_in:
        _enable_mcp_picker(page)
        try:
            flags = _type_and_send(page, CHAT_MESSAGE)
            page.wait_for_function(
                """() => {
                    const t = document.body ? document.body.innerText : '';
                    return t.includes('Yokohama') || t.includes('No MCP tools')
                        || t.includes('Kanagawa') || t.includes('error');
                }""",
                timeout=45_000,
            )
            assistant_visible = True
        except Exception:
            assistant_visible = False
        ui_text = page.locator("body").inner_text()

    classified = classify_observation(
        input_found=flags["input_found"],
        send_clicked=flags["send_clicked"],
        logged_in=logged_in,
        assistant_visible=assistant_visible,
        openai_saw_tools=openai_request_had_tools(OPENAI_LOG),
        mcp_calls=load_mcp_log(MCP_LOG),
        ui_text=ui_text,
    )
    record = write_observation(
        OBSERVED,
        observation={
            **classified,
            "chat_message": CHAT_MESSAGE,
            "librechat_base_url": LIBRECHAT_BASE_URL,
            "mcp_call_count": len(load_mcp_log(MCP_LOG)),
            "openai_saw_tools": openai_request_had_tools(OPENAI_LOG),
        },
    )
    assert record["verdict"] in {VERDICT_PASS, "ANTIPATTERN"}
    assert OBSERVED.is_file()
    # A miss is the point of this test: keep CI green so we can stack records.
    # The send-click test above is what fails when the UI itself is broken.
    if classified["verdict"] != VERDICT_PASS:
        print(f"recorded anti-pattern {classified['antipattern_id']}: {classified['detail']}")
