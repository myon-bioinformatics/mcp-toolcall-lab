"""JSONL log archive: grep parity, integrity, and mutation detection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_toolcall_lab.jsonl_log_archive import (
    ArchiveError,
    JsonlArchive,
    build_archive,
)

FIXTURE = Path(__file__).parent / "fixtures" / "jsonl_log_archive_sample.jsonl"


def _grep_lines(path: Path, needle: str) -> list[tuple[int, str]]:
    out = subprocess.run(
        ["grep", "-F", "-n", needle, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    hits: list[tuple[int, str]] = []
    for row in out.stdout.splitlines():
        if not row:
            continue
        num, rest = row.split(":", 1)
        hits.append((int(num), rest))
    return hits


def _build_tmp(tmp_path: Path, lines: list[str]) -> tuple[Path, Path]:
    src = tmp_path / "ledger.jsonl"
    src.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    arc = tmp_path / "ledger.jlogarc"
    build_archive(src, arc)
    return src, arc


@pytest.fixture()
def sample_archive(tmp_path: Path) -> tuple[Path, Path]:
    text = FIXTURE.read_text(encoding="utf-8")
    src = tmp_path / "sample.jsonl"
    src.write_text(text, encoding="utf-8")
    arc = tmp_path / "sample.jlogarc"
    build_archive(src, arc)
    return src, arc


@pytest.mark.parametrize(
    "needle",
    [
        "tools/call",
        "find_municipalities",
        "日本語テスト",
        "bc-demoagent0001",
        "decision=merge",
        "pull/42",
        "abcdef0123456789abcdef0123456789abcdef01",
        "cafebab",
        "稀有語",
        "ANTIPATTERN",
    ],
)
def test_search_matches_grep(sample_archive: tuple[Path, Path], needle: str) -> None:
    src, arc = sample_archive
    want = _grep_lines(src, needle)
    got = JsonlArchive(arc).search(needle)
    assert got == want


def test_each_block_decompresses_independently(sample_archive: tuple[Path, Path]) -> None:
    _, arc = sample_archive
    archive = JsonlArchive(arc)
    data_start, entries = archive._blocks_info
    raw = archive._section(1)
    for block_id, (rel_off, size, _crc) in enumerate(entries):
        start = data_start + rel_off
        payload = raw[start : start + size]
        lines = archive.decompress_block(block_id)
        assert isinstance(lines, list)


def test_broken_archive_detected(tmp_path: Path) -> None:
    src, arc = _build_tmp(tmp_path, ['{"event":"tools/call","tool":"ping"}'])
    blob = bytearray(arc.read_bytes())
    blob[120] ^= 0xFF
    bad = tmp_path / "broken.jlogarc"
    bad.write_bytes(blob)
    with pytest.raises(ArchiveError, match="digest|crc|zlib|magic"):
        JsonlArchive(bad)


def test_get_line_and_id_lookup(sample_archive: tuple[Path, Path]) -> None:
    src, arc = sample_archive
    archive = JsonlArchive(arc)
    assert "tools/call" in archive.get_line(1)
    pr_lines = archive.lookup_id("42")
    assert pr_lines
    assert "pull/42" in archive.get_line(pr_lines[0])
    sha_lines = archive.lookup_id("abcdef0123456789abcdef0123456789abcdef01")
    assert sha_lines
    bc_lines = archive.lookup_id("bc-demoagent0001")
    assert bc_lines


def test_summary_nonempty(sample_archive: tuple[Path, Path]) -> None:
    _, arc = sample_archive
    summary = JsonlArchive(arc).summary
    assert "tools/call" in summary
    assert "PR#42" in summary


@pytest.mark.skipif(
    not os.environ.get("JLOGARC_INTEGRITY_PROBE"),
    reason="subprocess mutation probe only",
)
def test_env_archive_must_be_valid() -> None:
    path = Path(os.environ["JLOGARC_INTEGRITY_PROBE"])
    JsonlArchive(path)


def test_mutation_subprocess_fails_integrity(tmp_path: Path) -> None:
    src, arc = _build_tmp(
        tmp_path,
        [
            '{"event":"tools/call","tool":"ping","note":"日本語"}',
            '{"event":"tools/call","tool":"pong","id":"bc-demoagent0099"}',
        ],
    )
    mutated = tmp_path / "mutated.jlogarc"
    data = bytearray(arc.read_bytes())
    data[len(data) // 2] ^= 0xFF
    mutated.write_bytes(data)
    env = {
        **os.environ,
        "JLOGARC_INTEGRITY_PROBE": str(mutated),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"{__file__}::test_env_archive_must_be_valid",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr


def test_build_reports_stats(tmp_path: Path) -> None:
    rows = [
        json.dumps({"event": "tools/call", "tool": "ping", "outcome": "success"})
        for _ in range(300)
    ]
    src = tmp_path / "medium.jsonl"
    src.write_text("\n".join(rows) + "\n", encoding="utf-8")
    arc = tmp_path / "medium.jlogarc"
    stats = build_archive(src, arc)
    assert stats["lines"] == 300
    assert stats["archive_bytes"] > 0
    assert JsonlArchive(arc).line_count == 300
