"""Compressed JSONL ledger archive with n-gram block index (stdlib only).

Builds a single-file archive from MCP-style JSONL logs (``MCP_TOOLCALL_LOG`` and
similar): LZMA block compression, zlib+varint reverse index, direct ID lookup,
and a plain-text summary layer. CLI: build / search / get / summary.
Version 2 indexes raw literal grams for exact substring search. Version 1
archives remain readable; their normalized index requires full-scan search.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import lzma
import os
import re
import struct
import sys
import unicodedata
import zlib
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

MAGIC = b"JLOGARC1"
FORMAT_VERSION = 2
BLOCK_LINES = 128
FOOTER_SIZE = 32

_STOP = re.compile(r"[、。・（）()「」『』:：,，\s?？!！]+")
_RE_PR = re.compile(
    r"(?:#(\d{1,9})|pull/(\d{1,9})|github\.com/[^/\s]+/[^/\s]+/pull/(\d{1,9}))",
    re.IGNORECASE,
)
_RE_SHA40 = re.compile(r"\b[0-9a-fA-F]{40}\b")
_RE_SHA7 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7}(?![0-9a-fA-F])")
_RE_BC = re.compile(r"\bbc-[0-9a-zA-Z]{8,}\b")

_SECTION_BLOCKS = 1
_SECTION_INDEX = 2
_SECTION_IDS = 3
_SECTION_SUMMARY = 4
_SECTION_SIM = 5


class ArchiveError(Exception):
    """Invalid or corrupted archive."""


def norm(text: str) -> str:
    return _STOP.sub(" ", unicodedata.normalize("NFKC", text)).strip().lower()


def ngrams_23(text: str) -> list[str]:
    t = norm(text)
    out: list[str] = []
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            g = t[i : i + n]
            if " " not in g:
                out.append(g)
    return out


def _literal_ngrams(text: str) -> set[str]:
    """Raw grams preserve substring membership, including Unicode boundaries."""
    return {text[i : i + n] for n in (2, 3) for i in range(len(text) - n + 1)}


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    buf = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            break
    return bytes(buf)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    shift = 0
    result = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, offset
        shift += 7
        if shift > 63:
            raise ArchiveError("varint overflow")
    raise ArchiveError("truncated varint")


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if not text:
        return []
    # JSONL records are separated by LF only. Unicode line separators such as
    # U+2028/U+2029/U+0085 are legal within a JSON string, not record breaks.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _pack_header(
    *,
    line_count: int,
    source_bytes: int,
    block_count: int,
    sections: dict[int, tuple[int, int]],
) -> bytes:
    parts = [
        MAGIC,
        struct.pack("<H", FORMAT_VERSION),
        struct.pack("<I", BLOCK_LINES),
        struct.pack("<Q", line_count),
        struct.pack("<Q", source_bytes),
        struct.pack("<I", block_count),
        struct.pack("<I", len(sections)),
    ]
    for kind in sorted(sections):
        off, size = sections[kind]
        parts.append(struct.pack("<IIQ", kind, size, off))
    return b"".join(parts)


def _parse_header(blob: bytes) -> dict[str, Any]:
    if len(blob) < 8 + 2 + 4 + 8 + 8 + 4 + 4:
        raise ArchiveError("header too short")
    if blob[:8] != MAGIC:
        raise ArchiveError("bad magic")
    version = struct.unpack_from("<H", blob, 8)[0]
    if version not in (1, FORMAT_VERSION):
        raise ArchiveError(f"unsupported version {version}")
    block_lines = struct.unpack_from("<I", blob, 10)[0]
    line_count = struct.unpack_from("<Q", blob, 14)[0]
    source_bytes = struct.unpack_from("<Q", blob, 22)[0]
    block_count = struct.unpack_from("<I", blob, 30)[0]
    nsec = struct.unpack_from("<I", blob, 34)[0]
    pos = 38
    sections: dict[int, tuple[int, int]] = {}
    for _ in range(nsec):
        if pos + 16 > len(blob):
            raise ArchiveError("truncated section table")
        kind, size, off = struct.unpack_from("<IIQ", blob, pos)
        sections[kind] = (off, size)
        pos += 16
    return {
        "version": version,
        "header_size": pos,
        "block_lines": block_lines,
        "line_count": line_count,
        "source_bytes": source_bytes,
        "block_count": block_count,
        "sections": sections,
    }


def _verify_footer(blob: bytes) -> None:
    if len(blob) < FOOTER_SIZE:
        raise ArchiveError("missing footer")
    digest = blob[-FOOTER_SIZE:]
    body = blob[:-FOOTER_SIZE]
    if hashlib.sha256(body).digest() != digest:
        raise ArchiveError("footer digest mismatch")


def _compress_block(lines: Sequence[str]) -> bytes:
    payload = "\n".join(lines).encode("utf-8")
    if lines:
        payload += b"\n"
    return lzma.compress(payload, format=lzma.FORMAT_XZ)


def _decompress_block(data: bytes) -> list[str]:
    try:
        raw = lzma.decompress(data)
    except lzma.LZMAError as exc:
        raise ArchiveError(f"block decompress failed: {exc}") from exc
    text = raw.decode("utf-8")
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _serialize_index(postings: dict[str, set[int]]) -> bytes:
    chunks: list[bytes] = []
    for key in sorted(postings):
        grams = key.encode("utf-8")
        blocks = sorted(postings[key])
        row = encode_varint(len(grams)) + grams
        row += encode_varint(len(blocks))
        for b in blocks:
            row += encode_varint(b)
        chunks.append(row)
    payload = encode_varint(len(chunks)) + b"".join(chunks)
    return zlib.compress(payload, level=9)


def _deserialize_index(data: bytes) -> dict[str, list[int]]:
    try:
        payload = zlib.decompress(data)
    except zlib.error as exc:
        raise ArchiveError(f"index zlib failed: {exc}") from exc
    count, pos = decode_varint(payload, 0)
    postings: dict[str, list[int]] = {}
    for _ in range(count):
        glen, pos = decode_varint(payload, pos)
        gram = payload[pos : pos + glen].decode("utf-8")
        pos += glen
        blen, pos = decode_varint(payload, pos)
        blocks: list[int] = []
        for _ in range(blen):
            b, pos = decode_varint(payload, pos)
            blocks.append(b)
        postings[gram] = blocks
    return postings


def _collect_ids(line_no: int, line: str, table: dict[str, list[int]]) -> None:
    for m in _RE_PR.finditer(line):
        num = next(g for g in m.groups() if g)
        key = f"pr:{num}"
        table.setdefault(key, []).append(line_no)
    for m in _RE_SHA40.finditer(line):
        key = f"sha40:{m.group(0).lower()}"
        table.setdefault(key, []).append(line_no)
    for m in _RE_SHA7.finditer(line):
        key = f"sha7:{m.group(0).lower()}"
        table.setdefault(key, []).append(line_no)
    for m in _RE_BC.finditer(line):
        key = f"bc:{m.group(0)}"
        table.setdefault(key, []).append(line_no)


def _build_summary(lines: Sequence[str]) -> str:
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(f"L{i}: (non-json) {line[:200]}")
            continue
        event = row.get("event") or row.get("type") or "?"
        tool = row.get("tool") or row.get("name") or ""
        outcome = row.get("outcome") or row.get("status") or ""
        bits = [f"L{i}", str(event)]
        if tool:
            bits.append(f"tool={tool}")
        if outcome:
            bits.append(f"outcome={outcome}")
        decision = row.get("decision") or row.get("note")
        if isinstance(decision, str) and decision.strip():
            bits.append(f"decision={decision.strip()[:120]}")
        ids: list[str] = []
        for m in _RE_PR.finditer(line):
            num = next(g for g in m.groups() if g)
            ids.append(f"PR#{num}")
        for m in _RE_SHA40.finditer(line):
            ids.append(f"sha40:{m.group(0).lower()[:12]}…")
        for m in _RE_BC.finditer(line):
            ids.append(m.group(0))
        if ids:
            bits.append("ids=" + ",".join(ids))
        out.append(" | ".join(bits))
    return "\n".join(out) + ("\n" if out else "")


def _sim_projection_seed() -> bytes:
    return hashlib.sha256(b"jlogarc-sim-v1").digest()


def _sim_hash_feature(gram: str, dim: int, seed: bytes) -> tuple[int, int]:
    h = hashlib.blake2b(gram.encode("utf-8"), digest_size=16, key=seed)
    word = int.from_bytes(h.digest()[:8], "little", signed=False)
    idx = word % dim
    sign = 1 if (word >> 63) & 1 else -1
    return idx, sign


def _sim_sign(text: str, dim: int = 128, seed: bytes | None = None) -> bytes:
    seed = seed or _sim_projection_seed()
    vec = [0] * dim
    for g in ngrams_23(text):
        idx, sign = _sim_hash_feature(g, dim, seed)
        vec[idx] += sign
    bits = bytearray((dim + 7) // 8)
    for i, v in enumerate(vec):
        if v > 0:
            bits[i // 8] |= 1 << (i % 8)
    return bytes(bits)


def _sim_hamming(a: bytes, b: bytes) -> int:
    dist = 0
    for x, y in zip(a, b, strict=True):
        dist += (x ^ y).bit_count()
    return dist


def build_archive(
    source: Path,
    dest: Path,
    *,
    include_sim: bool = False,
) -> dict[str, Any]:
    lines = _read_lines(source)
    source_bytes = source.read_bytes()
    postings: dict[str, set[int]] = defaultdict(set)
    id_table: dict[str, list[int]] = {}
    sim_rows: list[bytes] = []

    blocks_meta: list[tuple[int, int, int]] = []
    block_payloads: list[bytes] = []
    offset = 0

    block_count = (len(lines) + BLOCK_LINES - 1) // BLOCK_LINES if lines else 0
    for block_id in range(block_count):
        chunk = lines[block_id * BLOCK_LINES : (block_id + 1) * BLOCK_LINES]
        compressed = _compress_block(chunk)
        crc = _crc32(compressed)
        blocks_meta.append((offset, len(compressed), crc))
        block_payloads.append(compressed)
        offset += len(compressed)
        base_line = block_id * BLOCK_LINES
        for rel, line in enumerate(chunk):
            line_no = base_line + rel + 1
            _collect_ids(line_no, line, id_table)
            for g in _literal_ngrams(line):
                postings[g].add(block_id)
            if include_sim:
                sim_rows.append(_sim_sign(line))

    blocks_blob = bytearray()
    blocks_blob += struct.pack("<I", block_count)
    for off, size, crc in blocks_meta:
        blocks_blob += struct.pack("<III", off, size, crc)
    blocks_blob += b"".join(block_payloads)

    index_blob = _serialize_index(postings)
    ids_blob = json.dumps(id_table, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    summary_blob = zlib.compress(_build_summary(lines).encode("utf-8"), level=9)
    sim_blob = b""
    if include_sim:
        sim_blob = b"".join(sim_rows)

    # layout: header | blocks | index | ids | summary | sim | footer
    header_placeholder = 256
    sections_order = [
        (_SECTION_BLOCKS, blocks_blob),
        (_SECTION_INDEX, index_blob),
        (_SECTION_IDS, ids_blob),
        (_SECTION_SUMMARY, summary_blob),
    ]
    if include_sim:
        sections_order.append((_SECTION_SIM, sim_blob))

    pos = header_placeholder
    section_map: dict[int, tuple[int, int]] = {}
    blobs: list[bytes] = []
    for kind, data in sections_order:
        section_map[kind] = (pos, len(data))
        blobs.append(data)
        pos += len(data)

    header = _pack_header(
        line_count=len(lines),
        source_bytes=len(source_bytes),
        block_count=block_count,
        sections=section_map,
    )
    if len(header) > header_placeholder:
        raise RuntimeError("header grew beyond placeholder")
    header = header.ljust(header_placeholder, b"\0")

    body = header + b"".join(blobs)
    archive = body + hashlib.sha256(body).digest()
    dest.write_bytes(archive)

    return {
        "lines": len(lines),
        "source_bytes": len(source_bytes),
        "archive_bytes": len(archive),
        "blocks": block_count,
        "ratio": len(archive) / len(source_bytes) if source_bytes else 0.0,
        "index_bytes": len(index_blob),
        "ids_keys": len(id_table),
        "include_sim": include_sim,
    }


class JsonlArchive:
    def __init__(self, path: Path):
        self.path = path
        self._blob = path.read_bytes()
        _verify_footer(self._blob)
        self.meta = _parse_header(self._blob)
        self._postings = _deserialize_index(self._section(_SECTION_INDEX))
        self._ids = json.loads(self._section(_SECTION_IDS).decode("utf-8"))
        try:
            summary = zlib.decompress(self._section(_SECTION_SUMMARY)).decode("utf-8")
        except zlib.error as exc:
            raise ArchiveError(f"summary zlib failed: {exc}") from exc
        self._summary = summary
        self._blocks_info = self._parse_blocks_section()

    def _section(self, kind: int) -> bytes:
        sections = self.meta["sections"]
        if kind not in sections:
            raise ArchiveError(f"missing section {kind}")
        off, size = sections[kind]
        return self._blob[off : off + size]

    def _parse_blocks_section(self) -> tuple[int, list[tuple[int, int, int]]]:
        raw = self._section(_SECTION_BLOCKS)
        if len(raw) < 4:
            raise ArchiveError("blocks section too short")
        count = struct.unpack_from("<I", raw, 0)[0]
        pos = 4
        entries: list[tuple[int, int, int]] = []
        data_start = 4 + count * 12
        for _ in range(count):
            off, size, crc = struct.unpack_from("<III", raw, pos)
            pos += 12
            payload_off = data_start + off
            payload = raw[payload_off : payload_off + size]
            if _crc32(payload) != crc:
                raise ArchiveError(f"block crc mismatch at index {len(entries)}")
            entries.append((off, size, crc))
        return data_start, entries

    @property
    def line_count(self) -> int:
        return int(self.meta["line_count"])

    @property
    def summary(self) -> str:
        return self._summary

    def decompress_block(self, block_id: int) -> list[str]:
        data_start, entries = self._blocks_info
        if block_id < 0 or block_id >= len(entries):
            raise ArchiveError("block id out of range")
        rel_off, size, _crc = entries[block_id]
        raw = self._section(_SECTION_BLOCKS)
        start = data_start + rel_off
        payload = raw[start : start + size]
        return _decompress_block(payload)

    def iter_lines(self) -> Iterator[tuple[int, str]]:
        block_lines = int(self.meta["block_lines"])
        for block_id in range(len(self._blocks_info[1])):
            lines = self.decompress_block(block_id)
            base = block_id * block_lines
            for rel, line in enumerate(lines):
                yield base + rel + 1, line

    def _candidate_blocks(self, needle: str) -> set[int] | None:
        # None means the index cannot decide; an empty set proves no match.
        # Version 1 normalized whole lines, which is not substring-preserving
        # (e.g. a combining mark after a needle can compose its final letter).
        if self.meta["version"] == 1:
            return None
        grams = _literal_ngrams(needle)
        if not grams:
            return None
        out: set[int] | None = None
        for gram in grams:
            blocks = self._postings.get(gram)
            if not blocks:
                return set()
            out = set(blocks) if out is None else out.intersection(blocks)
            if not out:
                return set()
        return out

    def search(self, needle: str) -> list[tuple[int, str]]:
        candidates = self._candidate_blocks(needle)
        hits: list[tuple[int, str]] = []
        if candidates is None:
            blocks = range(len(self._blocks_info[1]))
        else:
            blocks = sorted(candidates)
        for block_id in blocks:
            for line_no, line in self._iter_block_lines(block_id):
                if needle in line:
                    hits.append((line_no, line))
        hits.sort(key=lambda x: x[0])
        return hits

    def _iter_block_lines(self, block_id: int) -> Iterator[tuple[int, str]]:
        block_lines = int(self.meta["block_lines"])
        base = block_id * block_lines
        for rel, line in enumerate(self.decompress_block(block_id)):
            yield base + rel + 1, line

    def get_line(self, line_no: int) -> str:
        if line_no < 1 or line_no > self.line_count:
            raise ArchiveError("line number out of range")
        block_id = (line_no - 1) // int(self.meta["block_lines"])
        rel = (line_no - 1) % int(self.meta["block_lines"])
        lines = self.decompress_block(block_id)
        if rel >= len(lines):
            raise ArchiveError("line missing in block")
        return lines[rel]

    def lookup_id(self, token: str) -> list[int]:
        token = token.strip()
        keys: list[str] = []
        sha_prefix: str | None = None
        if token.startswith(("pr:", "sha40:", "sha7:", "bc:")):
            keys = [token]
        else:
            pr = re.fullmatch(r"(?:pr\s*#?|#|pull/)?(\d{1,9})", token, re.IGNORECASE)
            if pr:
                keys.append(f"pr:{pr.group(1)}")
            if re.fullmatch(r"[0-9a-fA-F]{7,40}", token):
                low = token.lower()
                if len(low) == 40:
                    keys.append(f"sha40:{low}")
                else:
                    sha_prefix = low
                    if len(low) == 7:
                        keys.append(f"sha7:{low}")
            elif token.startswith("bc-"):
                keys.append(f"bc:{token}")
            elif not keys:
                keys = [f"bc:{token}", token]
        if sha_prefix is not None:
            keys.extend(k for k in self._ids if k.startswith(f"sha40:{sha_prefix}"))
        seen: set[int] = set()
        for key in keys:
            seen.update(self._ids.get(key, []))
        return sorted(seen)

    def similar(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        if _SECTION_SIM not in self.meta["sections"]:
            return []
        raw = self._section(_SECTION_SIM)
        dim = 128
        row_bytes = (dim + 7) // 8
        if len(raw) % row_bytes:
            raise ArchiveError("sim section size mismatch")
        q = _sim_sign(query)
        scored: list[tuple[int, float]] = []
        for i in range(len(raw) // row_bytes):
            sig = raw[i * row_bytes : (i + 1) * row_bytes]
            dist = _sim_hamming(q, sig)
            scored.append((i + 1, 1.0 - dist / dim))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]


def cmd_build(args: argparse.Namespace) -> int:
    stats = build_archive(Path(args.input), Path(args.output), include_sim=args.sim)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    arc = JsonlArchive(Path(args.archive))
    for line_no, line in arc.search(args.query):
        print(f"{line_no}:{line}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    arc = JsonlArchive(Path(args.archive))
    if args.line is not None:
        print(arc.get_line(args.line))
        return 0
    if args.id is None:
        raise SystemExit("get requires --line or --id")
    lines = arc.lookup_id(args.id)
    if not lines:
        print(f"id not found: {args.id}", file=sys.stderr)
        return 1
    for line_no in lines:
        print(f"{line_no}:{arc.get_line(line_no)}")
    return 0


def cmd_similar(args: argparse.Namespace) -> int:
    arc = JsonlArchive(Path(args.archive))
    if _SECTION_SIM not in arc.meta["sections"]:
        print("archive has no similarity section (build with --sim)", file=sys.stderr)
        return 1
    for line_no, score in arc.similar(args.query, k=args.k):
        print(f"{line_no}\t{score:.3f}\t{arc.get_line(line_no)}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    arc = JsonlArchive(Path(args.archive))
    sys.stdout.write(arc.summary)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JSONL log archive (LZMA blocks + n-gram index)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="build archive from JSONL")
    p_build.add_argument("input", type=Path)
    p_build.add_argument("-o", "--output", required=True, type=Path)
    p_build.add_argument("--sim", action="store_true", help="include optional similarity signatures")
    p_build.set_defaults(func=cmd_build)

    p_search = sub.add_parser("search", help="grep-compatible substring search")
    p_search.add_argument("archive", type=Path)
    p_search.add_argument("query")
    p_search.set_defaults(func=cmd_search)

    p_get = sub.add_parser("get", help="fetch line by number or lookup id")
    p_get.add_argument("archive", type=Path)
    p_get.add_argument("--line", type=int)
    p_get.add_argument("--id")
    p_get.set_defaults(func=cmd_get)

    p_summary = sub.add_parser("summary", help="plain-text summary layer")
    p_summary.add_argument("archive", type=Path)
    p_summary.set_defaults(func=cmd_summary)

    p_similar = sub.add_parser("similar", help="approximate similar lines (needs build --sim)")
    p_similar.add_argument("archive", type=Path)
    p_similar.add_argument("query")
    p_similar.add_argument("-k", type=int, default=5)
    p_similar.set_defaults(func=cmd_similar)

    args = parser.parse_args(argv)
    try:
        rc = int(args.func(args))
        sys.stdout.flush()
        return rc
    except ArchiveError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (BrokenPipeError, OSError) as exc:
        if not isinstance(exc, BrokenPipeError) and exc.errno not in (errno.EPIPE, errno.EINVAL):
            raise
        # Reader (e.g. `head`) closed the pipe: stop quietly like grep does.
        # Drop buffered output so the interpreter's final flush cannot fail.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
