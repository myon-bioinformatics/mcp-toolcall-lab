"""Guard the LibreChat example config so the documented Streamable HTTP shape cannot drift."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "librechat.mcp.example.yaml"


def test_librechat_example_yaml_exists_and_targets_streamable_http() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "mcpServers:" in text
    assert "mcp-toolcall-lab:" in text
    assert "type: streamable-http" in text
    assert "url: http://127.0.0.1:8000/mcp" in text
    assert "requiresOAuth: false" in text
    assert "startup: true" in text
    # stdio-only knobs must not sneak into the HTTP example
    assert "command:" not in text
    assert "args:" not in text
    assert "librechat_mcp_mock.py" in text
    assert "openwebui_mcp_mock.py" not in text


def test_librechat_smoke_compose_uses_shared_docker_network() -> None:
    compose = (ROOT / "docker" / "librechat-smoke" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    yaml = (ROOT / "docker" / "librechat-smoke" / "librechat.yaml").read_text(encoding="utf-8")
    assert "name: mcp-toolcall-lab" in compose
    assert "mcp-mock:" in compose
    assert "openai-mock:" in compose
    assert "url: http://mcp-mock:8000/mcp" in yaml
    assert 'baseURL: "http://openai-mock:8090/v1"' in yaml
    assert "host.docker.internal" not in yaml
    dockerfile = (ROOT / "docker" / "librechat-smoke" / "Dockerfile.mcp").read_text(encoding="utf-8")
    assert "librechat_mcp_mock.py" in dockerfile
    assert "openwebui_mcp_mock.py" not in dockerfile
    openai_df = (ROOT / "docker" / "librechat-smoke" / "Dockerfile.openai").read_text(
        encoding="utf-8"
    )
    assert "src/mcp_toolcall_lab/mock/common.py" in openai_df
    assert "openai_toolcall_mock.py" in openai_df
    assert "server.py" not in openai_df


def test_librechat_docs_and_prompt_are_present() -> None:
    notes = (ROOT / "docs" / "librechat_mcp_notes.md").read_text(encoding="utf-8")
    assert "librechat_mcp_mock.py" in notes
    assert "http://mcp-mock:8000/mcp" in notes
    prompt = (ROOT / "system_prompts" / "strict_tool_selection_librechat.md").read_text(
        encoding="utf-8"
    )
    assert "LibreChat" in prompt
    assert "tools/list" in prompt


def test_librechat_smoke_workflow_tracks_the_librechat_named_mock() -> None:
    workflow = (ROOT / ".github" / "workflows" / "librechat-docker-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "librechat_mcp_mock.py" in workflow
    assert "pytest -q" in workflow
