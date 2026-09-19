"""Guard the Open WebUI smoke compose so Streamable HTTP + mock names cannot drift."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_openwebui_smoke_compose_uses_shared_docker_network() -> None:
    compose = (ROOT / "docker" / "openwebui-smoke" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    env = (ROOT / "docker" / "openwebui-smoke" / "env.smoke").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "openwebui-smoke" / "Dockerfile.mcp").read_text(
        encoding="utf-8"
    )
    assert "name: mcp-toolcall-lab-openwebui" in compose
    assert "mcp-mock:" in compose
    assert "openai-mock:" in compose
    assert "open-webui:" in compose
    assert "ghcr.io/open-webui/open-webui:" in compose
    assert "http://mcp-mock:8000/mcp" in env
    assert '"type":"mcp"' in env or '"type": "mcp"' in env
    assert "server:mcp:lab" in env
    assert "OPENAI_API_BASE_URL=http://openai-mock:8090/v1" in env
    assert "WEBUI_AUTH=False" in env
    assert "ENABLE_FORWARD_USER_INFO_HEADERS=True" in env
    assert "host.docker.internal" not in env
    assert "http://openai-mock:8090/v1" in compose
    assert "openwebui_mcp_mock.py" in dockerfile
    assert "librechat_mcp_mock.py" not in dockerfile
    openai_df = (ROOT / "docker" / "librechat-smoke" / "Dockerfile.openai").read_text(
        encoding="utf-8"
    )
    assert "openai_toolcall_mock.py" in openai_df
    assert compose.count("dockerfile: docker/librechat-smoke/Dockerfile.openai") == 1


def test_openwebui_docs_and_workflow_track_the_owui_named_mock() -> None:
    notes = (ROOT / "docs" / "openwebui_mcp_notes.md").read_text(encoding="utf-8")
    assert "openwebui_mcp_mock.py" in notes
    assert "http://mcp-mock:8000/mcp" in notes
    assert "workflow_dispatch" in notes
    workflow = (ROOT / ".github" / "workflows" / "openwebui-docker-smoke.yml").read_text(
        encoding="utf-8"
    )
    default_ci = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "openwebui_mcp_mock.py" in workflow
    assert "workflow_dispatch:" in workflow
    assert "tests/real_chat_ui/test_openwebui_docker.py" in workflow
    assert "run: pytest -q" in default_ci
    assert "test_openwebui_docker.py" not in default_ci


def test_openwebui_frontend_catalog_points_at_the_smoke_stack() -> None:
    from mcp_toolcall_lab.frontends import OPENWEBUI

    assert OPENWEBUI.compose_file == "docker/openwebui-smoke/docker-compose.yml"
    assert OPENWEBUI.mcp.compose_mcp_url == "http://mcp-mock:8000/mcp"
    assert OPENWEBUI.mcp.compose_openai_url == "http://openai-mock:8090/v1"
    assert OPENWEBUI.mcp.tool_key("find_municipalities") == "lab_find_municipalities"
    assert OPENWEBUI.composer.input.css() == "#chat-input"
    assert OPENWEBUI.composer.send.css() == "#send-message-button"
