"""JSONL log archive: literal search parity, integrity, and mutation detection."""

from __future__ import annotations

import hashlib
import json
import struct
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_toolcall_lab.jsonl_log_archive import (
    ArchiveError,
    JsonlArchive,
    build_archive,
    BLOCK_LINES,
    _literal_ngrams,
    _serialize_index,
    ngrams_23,
)

FIXTURE = Path(__file__).parent / "fixtures" / "jsonl_log_archive_sample.jsonl"


def _literal_lines(path: Path, needle: str) -> list[tuple[int, str]]:
    return [(i, line) for i, line in enumerate(
        (lambda text: text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"))(
            path.read_text(encoding="utf-8")), start=1
    ) if needle in line]


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
def test_search_matches_literal_scan(sample_archive: tuple[Path, Path], needle: str) -> None:
    src, arc = sample_archive
    want = _literal_lines(src, needle)
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


def _block_payload_offset(arc: Path) -> int:
    archive = JsonlArchive(arc)
    section_off, _size = archive.meta["sections"][1]
    data_start, entries = archive._blocks_info
    rel_off, size, _crc = entries[0]
    return section_off + data_start + rel_off + size // 2


def _run_probe(path: Path) -> subprocess.CompletedProcess[str]:
    test_id = f"{Path(__file__).resolve()}::test_env_archive_must_be_valid"
    env = {**os.environ, "JLOGARC_INTEGRITY_PROBE": str(path)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test_id],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )


def test_mutation_subprocess_fails_integrity(tmp_path: Path) -> None:
    _src, arc = _build_tmp(
        tmp_path,
        [
            '{"event":"tools/call","tool":"ping","note":"日本語"}',
            '{"event":"tools/call","tool":"pong","id":"bc-demoagent0099"}',
        ],
    )
    clean = _run_probe(arc)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    mutated = tmp_path / "mutated.jlogarc"
    data = bytearray(arc.read_bytes())
    # Flip a byte inside an LZMA block payload: only the footer digest and
    # the block CRC can catch this when the archive is opened.
    data[_block_payload_offset(arc)] ^= 0xFF
    mutated.write_bytes(data)
    broken = _run_probe(mutated)
    assert broken.returncode == 1, broken.stdout + broken.stderr


@pytest.mark.parametrize("token", ["42", "#42", "PR#42", "pr 42", "pull/42"])
def test_lookup_pr_forms(sample_archive: tuple[Path, Path], token: str) -> None:
    _, arc = sample_archive
    archive = JsonlArchive(arc)
    assert archive.lookup_id(token) == archive.lookup_id("pr:42") != []


def test_lookup_full_sha_by_prefix(tmp_path: Path) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    _src, arc = _build_tmp(tmp_path, ['{"a":1}', f'{{"sha":"{sha}"}}', '{"b":2}'])
    archive = JsonlArchive(arc)
    for n in (7, 8, 12, 39, 40):
        assert archive.lookup_id(sha[:n]) == [2]
        assert archive.lookup_id(sha[:n].upper()) == [2]
    assert archive.lookup_id("fffffff") == []


def _cli(*args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "src" / "mcp_toolcall_lab" / "jsonl_log_archive.py"), *args]


def test_cli_get_id_not_found_exit_1(sample_archive: tuple[Path, Path]) -> None:
    _, arc = sample_archive
    ok = subprocess.run(_cli("get", str(arc), "--id", "PR#42"), capture_output=True, text=True)
    assert ok.returncode == 0 and "pull/42" in ok.stdout
    miss = subprocess.run(_cli("get", str(arc), "--id", "#999999"), capture_output=True, text=True)
    assert miss.returncode == 1 and miss.stdout == ""


@pytest.mark.parametrize("separator", ["\\u2028", "\\u2029", "\\u0085"])
def test_jsonl_unicode_separators_remain_in_same_record(
    tmp_path: Path, separator: str
) -> None:
    # Input JSON strings legally contain these literal Unicode characters.
    # splitlines() would silently convert two JSONL records into three.
    lines = [
        json.dumps({"text": "alpha" + separator + "beta"}, ensure_ascii=False),
        json.dumps({"text": "next"}, ensure_ascii=False),
    ]
    source, archive_path = _build_tmp(tmp_path, lines)
    archive = JsonlArchive(archive_path)
    assert archive.line_count == 2
    assert list(archive.iter_lines()) == [(1, lines[0]), (2, lines[1])]
    assert archive.get_line(1) == lines[0]
    assert archive.search("beta") == [(1, lines[0])]
    assert _literal_lines(source, "beta") == [(1, lines[0])]


def test_cli_broken_pipe_is_quiet(tmp_path: Path) -> None:
    rows = [json.dumps({"i": i, "t": "needle " + "x" * 200}) for i in range(5000)]
    _src, arc = _build_tmp(tmp_path, rows)
    proc = subprocess.Popen(_cli("search", str(arc), "needle"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    proc.stdout.readline()
    proc.stdout.close()
    assert proc.wait(timeout=60) == 0
    assert proc.stderr is not None
    err = proc.stderr.read()
    proc.stderr.close()
    assert b"Traceback" not in err


def test_cli_similar(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    src.write_text('{"t":"alpha beta gamma"}\n{"t":"日本語のログ"}\n', encoding="utf-8")
    arc = tmp_path / "s.jlogarc"
    build_archive(src, arc, include_sim=True)
    res = subprocess.run(_cli("similar", str(arc), "日本語", "-k", "1"), capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("2\t")
    plain = tmp_path / "p.jlogarc"
    build_archive(src, plain)
    res2 = subprocess.run(_cli("similar", str(plain), "x"), capture_output=True, text=True)
    assert res2.returncode == 1


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


@pytest.fixture()
def multiblock_archive(tmp_path: Path) -> tuple[Path, JsonlArchive]:
    # Distinct blocks let tests detect full scans, empty intersections and
    # false positives where all grams occur but the literal does not.
    lines = (["ab filler"] * BLOCK_LINES + ["bc filler"] * BLOCK_LINES
             + ["unique ab bc marker"] * BLOCK_LINES)
    src, arc = _build_tmp(tmp_path, lines)
    return src, JsonlArchive(arc)


@pytest.mark.parametrize("needle, expected_blocks", [
    ("missing", []),             # missing posting
    ("ab bc", [2]),              # selective positive match
    ("ab bc marker", [2]),
    ("ab bc missing", []),
    ("a", [0, 1, 2]),            # too short for an index decision
    ("", [0, 1, 2]),             # empty literal matches every line
    (" ", [0, 1, 2]),
])
def test_search_limits_decompression(multiblock_archive, monkeypatch, needle, expected_blocks):
    src, archive = multiblock_archive
    expanded = []
    original = archive.decompress_block

    def tracked(block_id):
        expanded.append(block_id)
        return original(block_id)

    monkeypatch.setattr(archive, "decompress_block", tracked)
    assert archive.search(needle) == _literal_lines(src, needle)
    assert expanded == expected_blocks


def test_empty_posting_intersection_skips_decompression(tmp_path, monkeypatch):
    src, arc = _build_tmp(tmp_path, ["abc"] * BLOCK_LINES + ["bcd"] * BLOCK_LINES)
    archive = JsonlArchive(arc)
    assert all(g in archive._postings for g in _literal_ngrams("abcd"))
    expanded = []
    monkeypatch.setattr(archive, "decompress_block", lambda b: expanded.append(b))
    assert archive.search("abcd") == _literal_lines(src, "abcd") == []
    assert expanded == []


def test_candidate_false_positive_is_verified(tmp_path, monkeypatch):
    src, arc = _build_tmp(tmp_path, ["abc", "bcd"])
    archive = JsonlArchive(arc)
    expanded = []
    original = archive.decompress_block

    def tracked(block_id):
        expanded.append(block_id)
        return original(block_id)

    monkeypatch.setattr(archive, "decompress_block", tracked)
    assert archive.search("abcd") == _literal_lines(src, "abcd") == []
    assert expanded == [0]


def test_empty_archive(tmp_path, monkeypatch):
    _, arc = _build_tmp(tmp_path, [])
    archive = JsonlArchive(arc)
    expanded = []
    monkeypatch.setattr(archive, "decompress_block", lambda b: expanded.append(b))
    for needle in ("", "a", "absent"):
        assert archive.search(needle) == []
    assert expanded == []


@pytest.mark.parametrize("needle", ["xA", "ΟΣ", "日本", "ＡＢ", "ab", "AB", "!!", "a b"])
def test_unicode_and_exact_literal_semantics(tmp_path, needle):
    lines = [json.dumps({"note": note}, ensure_ascii=False) for note in
             ["xA\u030a", "ΟΣΑ", "日本語", "ＡＢ", "AB", "ab", "!!", "a b"]]
    src, arc = _build_tmp(tmp_path, lines)
    assert JsonlArchive(arc).search(needle) == _literal_lines(src, needle)


def test_version_one_normalized_index_falls_back(tmp_path, monkeypatch):
    # Construct an actual v1 normalized-only index, not a relabeled v2 one.
    src, arc = _build_tmp(tmp_path, ['{"note":"xA\u030a ΟΣΑ"}'])
    archive = JsonlArchive(arc)
    blob = bytearray(arc.read_bytes())
    index = _serialize_index({g: {0} for g in ngrams_23(src.read_text(encoding="utf-8"))})
    off, size = archive.meta["sections"][2]
    delta = len(index) - size
    blob[off:off + size] = index
    struct.pack_into("<H", blob, 8, 1)
    nsec = struct.unpack_from("<I", blob, 34)[0]
    for pos in range(38, 38 + nsec * 16, 16):
        kind, length, start = struct.unpack_from("<IIQ", blob, pos)
        if kind == 2:
            length = len(index)
        elif start > off:
            start += delta
        struct.pack_into("<IIQ", blob, pos, kind, length, start)
    blob[-32:] = hashlib.sha256(blob[:-32]).digest()
    arc.write_bytes(blob)
    legacy = JsonlArchive(arc)
    assert legacy._candidate_blocks("xA") is None
    assert legacy.search("xA") == _literal_lines(src, "xA")
    assert legacy.search("ΟΣ") == _literal_lines(src, "ΟΣ")
