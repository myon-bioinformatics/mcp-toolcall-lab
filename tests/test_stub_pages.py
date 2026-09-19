"""Pages/Actions stub stack — no Docker in default pytest."""

from __future__ import annotations

from pathlib import Path

from mcp_toolcall_lab.antipatterns import (
    CPU_LLM_UNREACHABLE,
    MCP_UNREACHABLE,
    classify_stub_turn,
)
from mcp_toolcall_lab.markdown_lib import load_markdown, markdown_py_path
from mcp_toolcall_lab.stub_front import parse_sections, render_rows, write_pages

ROOT = Path(__file__).resolve().parents[1]


def test_vendored_markdown_py_is_loadable() -> None:
    path = markdown_py_path()
    assert path is not None
    md = load_markdown()
    assert md is not None
    assert md.split_sections("# Yokohama\n\nbody\n")[0]["title"] == "Yokohama"


def test_parse_sections_uses_markdown_py_bodies() -> None:
    sections = parse_sections("# Find municipalities\n\nUse the mock tool.\n")
    assert sections[0].title == "Find municipalities"
    assert "mock tool" in sections[0].body


def test_render_rows_uses_markdown_table() -> None:
    text = render_rows([{"name": "Yokohama", "code": "14109"}])
    assert "Yokohama" in text
    assert "14109" in text


def test_write_pages_is_static(tmp_path: Path) -> None:
    last = tmp_path / "last-run.json"
    last.write_text('{"ok": true}\n', encoding="utf-8")
    index = write_pages(tmp_path / "site", last_run=last)
    html = index.read_text(encoding="utf-8")
    assert "mcp-toolcall-lab stub" in html
    assert "mcp-mock:8000/mcp" in html
    assert (tmp_path / "site" / "last-run.json").is_file()


def test_classify_stub_turn_pass_and_miss() -> None:
    ok = classify_stub_turn(
        {"case": "MCP_SUCCESS", "assistant": "Yokohama 14109"}, cpu_llm_ok=True
    )
    assert ok["verdict"] == "PASS"
    assert ok["cpu_llm_ok"] is True
    miss = classify_stub_turn({"case": "MCP_UNREACHABLE", "assistant": "timed out"})
    assert miss["antipattern_id"] == MCP_UNREACHABLE
    down = classify_stub_turn(
        {"case": "MCP_SUCCESS", "assistant": "Yokohama"}, cpu_llm_ok=False
    )
    assert down["cpu_llm_ok"] is False
    assert CPU_LLM_UNREACHABLE != down["antipattern_id"]


def test_stub_pages_compose_uses_service_dns() -> None:
    compose = (ROOT / "docker" / "stub-pages" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "name: mcp-toolcall-lab-pages" in compose
    assert "STUB_MCP_URL: http://mcp-mock:8000/mcp" in compose
    assert "STUB_CPU_LLM_URL: http://cpu-llm:8080/v1" in compose
    assert "host.docker.internal" not in compose
    stub_df = (ROOT / "docker" / "stub-pages" / "Dockerfile.stub").read_text(encoding="utf-8")
    assert "pip install" not in stub_df
    assert "vendor/markdown.py" in stub_df
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "actions/deploy-pages" in workflow
    assert "stub-pages-smoke.py" in workflow or "stub_pages_smoke.py" in workflow
