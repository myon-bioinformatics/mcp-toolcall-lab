"""Import/startup-level validation for the Gradio/Streamlit reference clients.

Normal-PR lane: no browser, no Docker -- just "does the app import and build
without crashing" (Gradio) and "does the script run start to finish"
(Streamlit's own headless ``AppTest`` harness). Full click-through coverage of
either UI lives in the separate, non-default browser-smoke lane (see
docs/e2e_foundation.md); this module stays fast enough for every PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_streamable_http_protocol import running_mcp_server

ROOT = Path(__file__).resolve().parents[1]


def test_gradio_app_builds_without_launching_a_server():
    import gradio as gr

    from apps.gradio_app import build_app

    demo = build_app()
    assert isinstance(demo, gr.Blocks)


@pytest.mark.integration
def test_gradio_reference_client_round_trip_against_a_live_mock(monkeypatch):
    from apps import gradio_app

    with running_mcp_server() as server:
        monkeypatch.setenv("MCP_URL", server.url)

        names = gradio_app.list_tool_names()
        assert sorted(names) == ["find_municipalities", "find_stations", "find_transaction_prices"]

        rendered = gradio_app.run_tool_call("Where is Yokohama?", "find_municipalities", '{"query": "Yokohama"}')

    assert '"outcome": "success"' in rendered
    assert "Yokohama" in rendered


def test_gradio_run_tool_call_rejects_invalid_arguments_json_without_calling_mcp():
    from apps.gradio_app import run_tool_call

    message = run_tool_call("prompt", "find_municipalities", "{not json")
    assert "Invalid arguments JSON" in message


def test_streamlit_app_module_imports_without_a_script_run_context():
    import apps.streamlit_app as streamlit_app

    assert callable(streamlit_app.main)


@pytest.mark.integration
def test_streamlit_app_starts_and_renders_without_exception(monkeypatch):
    from streamlit.testing.v1 import AppTest

    with running_mcp_server() as server:
        monkeypatch.setenv("MCP_URL", server.url)
        app_test = AppTest.from_file(str(ROOT / "apps" / "streamlit_app.py"))
        app_test.run()

    assert not app_test.exception


@pytest.mark.integration
def test_streamlit_app_discover_tools_button_reaches_a_live_mock(monkeypatch):
    from streamlit.testing.v1 import AppTest

    with running_mcp_server() as server:
        monkeypatch.setenv("MCP_URL", server.url)
        app_test = AppTest.from_file(str(ROOT / "apps" / "streamlit_app.py"))
        app_test.run()
        assert not app_test.exception

        app_test.button[0].click().run()
        assert not app_test.exception

        discovered = app_test.session_state["tool_names"]

    assert sorted(discovered) == ["find_municipalities", "find_stations", "find_transaction_prices"]
