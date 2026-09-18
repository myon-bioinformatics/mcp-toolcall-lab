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


def test_librechat_docs_and_prompt_are_present() -> None:
    assert (ROOT / "docs" / "librechat_mcp_notes.md").is_file()
    prompt = (ROOT / "system_prompts" / "strict_tool_selection_librechat.md").read_text(
        encoding="utf-8"
    )
    assert "LibreChat" in prompt
    assert "tools/list" in prompt
