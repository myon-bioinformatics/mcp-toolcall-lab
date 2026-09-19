"""Pages/Actions stub stack — no Docker in default pytest."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_toolcall_lab.antipatterns import (
    CPU_LLM_UNREACHABLE,
    MCP_UNREACHABLE,
    classify_stub_turn,
)
from mcp_toolcall_lab.markdown_lib import (
    assert_markdown_provenance,
    load_markdown,
    markdown_py_path,
)
from mcp_toolcall_lab.stub_front import (
    PAGES_FORBIDDEN_NAMES,
    PAGES_SUMMARY_KEYS,
    pages_summary,
    parse_sections,
    render_rows,
    write_pages,
)

ROOT = Path(__file__).resolve().parents[1]


def test_vendored_markdown_py_is_loadable() -> None:
    path = markdown_py_path()
    assert path is not None
    md = load_markdown()
    assert md is not None
    assert md.split_sections("# Yokohama\n\nbody\n")[0]["title"] == "Yokohama"
    recorded = assert_markdown_provenance()
    assert recorded["commit"] == "1c0f7b98c935457dca59b94b7c43af448431c40e"
    assert recorded["blob_sha"] == "4e621652ab49ea3c677216354a20647c867a7a47"
    readme = (ROOT / "vendor" / "README.md").read_text(encoding="utf-8")
    assert "ref=${COMMIT}" in readme
    assert "not `main`" in readme


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
    last.write_text('{"cpu_llm_ok": true, "stub_health": {"status": 200}}\n', encoding="utf-8")
    index = write_pages(tmp_path / "site", last_run=last)
    html = index.read_text(encoding="utf-8")
    assert "mcp-toolcall-lab stub" in html
    assert "mcp-mock:8000/mcp" in html
    assert (tmp_path / "site" / "summary.json").is_file()
    assert not (tmp_path / "site" / "last-run.json").exists()


def test_pages_tree_excludes_raw_mcp_logs(tmp_path: Path) -> None:
    last = tmp_path / "last-run.json"
    last.write_text(
        json.dumps(
            {
                "turn": {
                    "case": "MCP_SUCCESS",
                    "assistant": "Yokohama",
                    "arguments": {"query": "secret-prompt"},
                    "chat_id": "chat_secret",
                    "meta": {"chat_id": "chat_secret"},
                },
                "observation": {"verdict": "PASS", "antipattern_id": None},
                "cpu_llm_ok": True,
                "cpu_llm_backend": "lite-stub",
                "cpu_llm_completion_ok": True,
                "stub_health": {"status": 200, "body": "ok"},
                "turn_http": 200,
            }
        ),
        encoding="utf-8",
    )
    anti = tmp_path / "antipatterns.jsonl"
    anti.write_text(
        '{"verdict":"PASS","detail":"reached MCP","chat_id":"chat_secret"}\n',
        encoding="utf-8",
    )
    out = tmp_path / "site"
    out.mkdir()
    (out / "mcp-toolcalls.jsonl").write_text("{}\n", encoding="utf-8")
    write_pages(out, last_run=last, observations=anti)
    names = {path.name for path in out.iterdir()}
    assert names.isdisjoint(PAGES_FORBIDDEN_NAMES)
    public = (out / "index.html").read_text(encoding="utf-8") + (out / "summary.json").read_text(
        encoding="utf-8"
    )
    assert "secret-prompt" not in public
    assert "chat_secret" not in public
    assert '"arguments"' not in public
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert set(summary) == set(PAGES_SUMMARY_KEYS)
    assert summary["verdict"] == "PASS"
    assert summary["showed_expected_fragment"] is True
    assert summary["cpu_llm_backend"] == "lite-stub"
    assert summary["cpu_llm_completion_ok"] is True
    assert pages_summary({"turn": {"case": "HEADING_MISS"}}, [])["case"] == "HEADING_MISS"


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
    assert "_site/mcp-toolcalls.jsonl" not in workflow
    assert "_site/antipatterns.jsonl" not in workflow
