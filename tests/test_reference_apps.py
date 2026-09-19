"""Import/startup-level validation for the Gradio and Streamlit reference apps.

Per issue #14, the normal-PR lane needs "Gradio / Streamlit import and
startup-level validation" without booting any real chat product. Both apps
build against this repo's own tiny mock server (``running_mcp_server``, the
same subprocess every other integration test here already boots) — that is
not a "real chat product" in the issue's sense, so this stays in-lane.

Each test module-level import is skipped (not failed) when the optional
`gradio`/`streamlit` extras are not installed, matching the existing
`browser-test`/Playwright pattern in this repo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_streamable_http_protocol import running_mcp_server

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def test_gradio_app_builds_against_a_live_mock_server():
    gr = pytest.importorskip("gradio")
    from apps.gradio_app import build_app

    with running_mcp_server() as server:
        demo = build_app(server.url)

    assert isinstance(demo, gr.Blocks)


def test_gradio_tool_choices_match_advertised_tools():
    pytest.importorskip("gradio")
    from apps.gradio_app import _tool_choices

    with running_mcp_server() as server:
        choices = _tool_choices(server.url)

    assert set(choices) == {"find_municipalities", "find_transaction_prices", "find_stations"}


def test_streamlit_app_runs_without_error_against_a_live_mock_server():
    pytest.importorskip("streamlit")
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

    with running_mcp_server() as server:
        previous = os.environ.get("MCP_URL")
        os.environ["MCP_URL"] = server.url
        try:
            at = AppTest.from_file(str(ROOT / "apps" / "streamlit_app.py"))
            at.run()
        finally:
            if previous is None:
                os.environ.pop("MCP_URL", None)
            else:
                os.environ["MCP_URL"] = previous

    assert not at.exception
