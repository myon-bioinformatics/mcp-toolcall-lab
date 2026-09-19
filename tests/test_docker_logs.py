"""Compose log projection — timestamp / level / message, no new protocol."""

from __future__ import annotations

from pathlib import Path

from mcp_toolcall_lab.docker_logs import (
    digests_from_inspect,
    parse_compose_line,
    parse_compose_text,
    parse_docker_images_digests,
    redact,
    timeline_rows,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "fixtures" / "docker_logs" / "compose.sample.log"


def test_sample_compose_log_has_time_level_and_service() -> None:
    rows = parse_compose_text(SAMPLE.read_text(encoding="utf-8"))
    assert rows
    first = rows[0]
    assert first["at"].startswith("2026-09-19T12:55:10")
    assert first["level"] == "INFO"
    assert first["service"] == "open-webui"
    assert "Application startup complete" in first["message"]
    assert first["source"] == "docker-compose"

    mcp = [row for row in rows if row["service"] == "mcp-mock"]
    assert any("event=initialize" in row["message"] for row in mcp)
    assert any(row.get("level") == "INFO" for row in mcp)

    lc = [row for row in rows if row["service"] == "librechat"]
    assert lc and lc[0]["level"] == "INFO"
    assert "mcp-mock:8000/mcp" in lc[0]["message"]


def test_title_task_and_mcp_initialize_are_visible_as_product_text() -> None:
    rows = parse_compose_text(SAMPLE.read_text(encoding="utf-8"))
    messages = " ".join(row["message"] for row in rows)
    assert "### Task:" in messages
    assert "initialize" in messages
    assert "POST /mcp" in messages


def test_secrets_are_redacted_in_the_projection() -> None:
    hidden = redact("Authorization: Bearer sk-mcp-toolcall-lab-example")
    assert "sk-mcp" not in hidden
    assert "[REDACTED]" in hidden
    line = parse_compose_line(
        "open-webui-1  | 2026-09-19T12:55:12.000000000Z ERROR failed with Authorization: Bearer sk-mcp-toolcall-lab-example"
    )
    assert line is not None
    assert "sk-mcp" not in line["message"]
    assert line["level"] == "ERROR"


def test_timeline_orders_mcp_jsonl_with_compose_lines(tmp_path: Path) -> None:
    compose = tmp_path / "compose.jsonl"
    mcp = tmp_path / "mcp-toolcalls.jsonl"
    compose.write_text(
        '{"at":"2026-09-19T12:55:10.140Z","message":"startup","service":"open-webui"}\n',
        encoding="utf-8",
    )
    mcp.write_text(
        '{"at":"2026-09-19T12:55:10.154Z","event":"initialize"}\n'
        '{"at":"2026-09-19T12:55:10.174Z","event":"tools/list"}\n',
        encoding="utf-8",
    )
    rows = timeline_rows([compose, mcp])
    assert [row.get("event") or row.get("message") for row in rows] == [
        "startup",
        "initialize",
        "tools/list",
    ]
    assert rows[1]["stream"] == "mcp"


def test_floating_tag_digest_is_collected_from_inspect_and_images_listing() -> None:
    inspect = {
        "RepoDigests": [],
        "Config": {"Image": "ghcr.io/open-webui/open-webui:main"},
        "Image": "sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    assert digests_from_inspect(inspect) == []
    inspect["RepoDigests"] = ["ghcr.io/open-webui/open-webui@sha256:" + "a" * 64]
    assert digests_from_inspect(inspect) == [
        "ghcr.io/open-webui/open-webui@sha256:" + "a" * 64
    ]
    listing = (
        "ghcr.io/open-webui/open-webui:main\tsha256:" + "b" * 64
        + "\tsha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        "unrelated/image:latest\tsha256:" + "c" * 64 + "\tsha256:ffff\n"
        "ghcr.io/open-webui/open-webui:main\t<none>\tsha256:deadbeef\n"
    )
    found = parse_docker_images_digests(
        listing,
        image_name="ghcr.io/open-webui/open-webui:main",
        image_id="sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    assert found == ["ghcr.io/open-webui/open-webui@sha256:" + "b" * 64]
