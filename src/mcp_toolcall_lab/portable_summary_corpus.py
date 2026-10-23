"""Local-only, incremental summary corpus with strict JSONL and lazy ZIP shards.

No networking, model invocation, publication, scheduling, or automatic review.
Content hashes prove consistency, not provenance/authenticity or permission.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sqlite3
import struct
import tempfile
from typing import Any, BinaryIO, Iterator
import uuid
import zipfile
import zlib

from .portable_execution import canonical_request, execute, request_sha256

MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
MAX_LOG_LINES = 1000
MAX_SHARD_BYTES = 8 * 1024 * 1024
MAX_SHARD_RECORDS = 256
MAX_ARCHIVE_BYTES = MAX_SHARD_BYTES + 65536
MAX_ARCHIVE_MEMBERS = 1
MAX_IMPORT_BYTES = 64 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
SHARD = re.compile(r"[0-9a-f]{32}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
KINDS = {"cases", "results", "logs"}


class CorpusError(ValueError):
    """Invalid, corrupt, unsafe, conflicting, or incomplete local corpus data."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CorpusError(message)


def _same(left: Any, right: Any) -> bool:
    """Canonical JSON equality does not confuse bools with integers."""
    return canonical_request(left) == canonical_request(right)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: Any) -> str:
    _json_budget(value)
    return request_sha256(value)


def _json_budget(value: Any) -> None:
    nodes = 0
    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        _need(depth <= 32 and nodes <= 100000, "JSON structure exceeds its budget.")
        if isinstance(item, str):
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise CorpusError("JSON contains invalid Unicode.") from exc
        elif item is None or type(item) is bool:
            return
        elif type(item) in (int, float):
            _need(abs(item) <= 2**53 - 1 and math.isfinite(item), "JSON number is not finite/safe.")
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, dict):
            for key, child in item.items():
                _need(isinstance(key, str) and len(key) <= 256, "JSON object key is invalid.")
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise CorpusError("Only ordinary JSON values are supported.")
    visit(value, 0)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _need(key not in result, "Duplicate JSON object key.")
        result[key] = value
    return result


def _parse(raw: bytes, *, limit: int = MAX_RECORD_BYTES) -> dict[str, Any]:
    _need(0 < len(raw) <= limit, "JSON record exceeds its byte budget or is empty.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CorpusError("Non-finite JSON number.")))
        _json_budget(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise CorpusError("Malformed UTF-8 JSON record.") from exc
    _need(isinstance(value, dict), "JSON record must be an object.")
    return value


def _copy(value: Any) -> Any:
    _json_budget(value)
    try:
        raw = canonical_request(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CorpusError("Invalid JSON value.") from exc
    _need(len(raw) <= MAX_RECORD_BYTES, "JSON record exceeds its byte budget.")
    return _parse(raw)


def _read_file(path: Path, limit: int) -> bytes:
    _need(not path.is_symlink() and path.is_file(), "Expected a regular local file, not a symlink.")
    _need(path.stat().st_size <= limit, "Local file exceeds its byte budget.")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    _need(len(raw) <= limit, "Local file exceeds its byte budget.")
    return raw


def _lines(handle: BinaryIO, *, total_limit: int) -> Iterator[tuple[int, bytes, dict[str, Any]]]:
    offset = 0
    while True:
        raw = handle.readline(MAX_RECORD_BYTES + 1)
        if not raw:
            return
        _need(len(raw) <= MAX_RECORD_BYTES and raw.endswith(b"\n"), "Oversized or unterminated JSONL record.")
        _need(raw.strip() != b"", "Blank JSONL records are not allowed.")
        _need(offset + len(raw) <= total_limit, "JSONL input exceeds its byte budget.")
        yield offset, raw, _parse(raw)
        offset += len(raw)


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Bounded streaming reader. Never silently skip malformed or blank records."""
    path = Path(path)
    _need(not path.is_symlink() and path.is_file(), "Expected a regular local JSONL file.")
    _need(path.stat().st_size <= MAX_IMPORT_BYTES, "JSONL import exceeds 64 MiB; split it first.")
    with path.open("rb") as handle:
        for _, _, row in _lines(handle, total_limit=MAX_IMPORT_BYTES):
            yield row


def _review(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    _need(isinstance(value, dict) and set(value) == {"classification", "reviewed_by", "reviewed_at"}, "Invalid public-review declaration.")
    _need(value["classification"] == "reviewed_public", "Review classification must be reviewed_public.")
    _need(isinstance(value["reviewed_by"], str) and 0 < len(value["reviewed_by"].strip()) <= 160, "Review must identify its reviewer.")
    try:
        timestamp = datetime.fromisoformat(value["reviewed_at"])
        _need(timestamp.tzinfo is not None, "Review time must include a timezone.")
    except (TypeError, ValueError) as exc:
        raise CorpusError("Invalid review timestamp.") from exc
    return value


def _resolve_selection(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate first, then remove only nonsemantic selection-array ordering."""
    request = _copy(request)
    _need(request.get("operation") == "source_selection", "Expected source_selection request.")
    result = execute(request)
    _need(result.get("status") == "ok" and result.get("selection_status") == "ready", "Invalid source selection or no selected evidence.")
    # Shared analysis returns keywords in original first-occurrence order and
    # selected sentences in source order. Never sort/normalize source text or
    # salvage stale/duplicate IDs before the analyzer has validated the request.
    request["input"]["keywords"] = [item["id"] for item in result["selected_keywords"]]
    request["input"]["sentence_ids"] = [item["id"] for item in result["selected_sentences"]]
    return request, result


def normalize_selection_request(request: dict[str, Any]) -> dict[str, Any]:
    """Canonical case request: exact text, keyword/source order, validated IDs.

    This does not change full model-specific summary-request hashing. Call it
    when constructing corpus cases, not to rewrite an existing execution receipt.
    The caller's object is never mutated.
    """
    return _resolve_selection(request)[0]


def make_case(request: dict[str, Any], request_id: str, *, review: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate source-bound IDs and canonicalize only case selection ordering."""
    _need(isinstance(request_id, str) and REQUEST_ID.fullmatch(request_id) is not None, "Invalid request_id (use 1-128 ASCII letters/digits/._:-).")
    request, result = _resolve_selection(request)
    provenance = result["provenance"]
    return _copy({
        "schema": "lab-summary-corpus-case/1", "request_id": request_id,
        "source_selection_request": request, "source_selection_request_sha256": _hash(request),
        "source_sha256": provenance["input_sha256"], "source_bytes": provenance["input_bytes"],
        "offset_unit": "utf16_code_units", "selected_source_evidence": result["selected_sentences"],
        "review": _review(review),
    })


def validate_case(case: dict[str, Any]) -> dict[str, Any]:
    case = _copy(case)
    _need(case.get("schema") == "lab-summary-corpus-case/1", "Unknown corpus case schema/version.")
    try:
        checked = make_case(case["source_selection_request"], case["request_id"], review=case["review"])
    except KeyError as exc:
        raise CorpusError("Incomplete corpus case.") from exc
    _need(_same(case, checked), "Case source hashes, offsets, evidence, or fields do not match its request.")
    return checked


def _validate_observation(row: dict[str, Any]) -> None:
    """Every exported log row is closed-schema: no source/text/extra metadata."""
    fields = {"schema", "event", "model_inference_performed", "source_selection_request_sha256",
              "summary_request_sha256", "wire_request_sha256", "generated_text_sha256", "usage",
              "elapsed_ms", "finish_reason", "generated_at"}
    _need(set(row) == fields and row["schema"] == "lab-summary-observation/1"
          and row["event"] == "portable/summary_generation" and row["model_inference_performed"] is True,
          "Unknown observation-log fields/schema; extra data must not be published.")
    _need(all(isinstance(row[field], str) and SHA.fullmatch(row[field]) is not None for field in
              ("source_selection_request_sha256", "summary_request_sha256", "wire_request_sha256", "generated_text_sha256")),
          "Invalid observation content hashes.")
    usage = row["usage"]
    _need(isinstance(usage, dict) and set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
          and all(type(value) is int and value > 0 for value in usage.values())
          and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], "Invalid observation token usage.")
    _need(type(row["elapsed_ms"]) is int and row["elapsed_ms"] >= 0 and row["finish_reason"] in {"stop", "length"},
          "Invalid observation timing/finish.")
    try:
        _need(isinstance(row["generated_at"], str) and len(row["generated_at"]) <= 64
              and datetime.fromisoformat(row["generated_at"]).tzinfo is not None, "Invalid observation timestamp.")
    except (ValueError, TypeError) as exc:
        if isinstance(exc, CorpusError):
            raise
        raise CorpusError("Invalid observation timestamp.") from exc


def _artifact_log(artifact: dict[str, Any], raw: bytes) -> dict[str, Any]:
    _need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_LOG_BYTES, "A bounded original observation log is required.")
    _need(b"\r" not in raw, "Observation logs must use LF newlines, matching browser/Node verification.")
    log = artifact["receipt"].get("observation_log")
    _need(isinstance(log, dict) and set(log) == {"path", "sha256", "line", "line_sha256"}, "Missing observation-log reference.")
    _need(isinstance(log["sha256"], str) and SHA.fullmatch(log["sha256"]) is not None and _digest(raw) == log["sha256"], "Observation-log checksum mismatch.")
    _need(log["path"] in {"summary-build.jsonl", f"summary-logs/{log['sha256']}.jsonl"}, "Unsafe observation-log path.")
    _need(type(log["line"]) is int and 1 <= log["line"] <= MAX_LOG_LINES, "Invalid observation-log line.")
    found = None
    count = 0
    for count, (_, line, row) in enumerate(_lines(io.BytesIO(raw), total_limit=MAX_LOG_BYTES), 1):
        _need(count <= MAX_LOG_LINES, "Too many observation-log records.")
        _validate_observation(row)
        if count == log["line"]:
            _need(_digest(line.rstrip(b"\n")) == log["line_sha256"], "Observation-log line checksum mismatch.")
            found = row
    _need(found is not None, "Observation-log line is missing.")
    return found


def validate_summary_artifact(artifact: dict[str, Any], *, log_bytes: bytes | None = None,
                              expected_summary_request: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify a successful artifact plus original log, without running a model.

    The complete stored recipe is bound to its hash. An expected request enforces
    the current build recipe; old recipes remain readable but cannot match it.
    These checks do not authenticate a producer or assess summary quality.
    """
    artifact = _copy(artifact)
    try:
        _need(set(artifact) == {"schema", "source_selection_request", "source_selection_request_sha256",
                               "summary_request", "summary_request_sha256", "receipt", "build"}
              and artifact.get("schema") == "lab-source-summary-artifact/1", "Unknown summary artifact fields/schema/version.")
        selection, request, receipt = artifact["source_selection_request"], artifact["summary_request"], artifact["receipt"]
        case = make_case(selection, "validate-artifact")
        selection_key, key = _hash(selection), _hash(request)
        _need(artifact["source_selection_request_sha256"] == selection_key and artifact["summary_request_sha256"] == key, "Artifact request checksum mismatch.")
        _need(request["schema"] == "lab-summary-request/1" and request["source_selection_request_sha256"] == selection_key, "Invalid summary request identity/schema.")
        for field in ("source_sha256", "source_bytes", "offset_unit", "selected_source_evidence"):
            _need(_same(request[field], case[field]), "Summary source evidence/offset mismatch.")
        _need(_same(request["selected_keyword_ids"], selection["input"]["keywords"]), "Summary keyword selection mismatch.")
        required_recipe = {"model", "runtime", "sampler", "generation", "server", "prompt_version", "system_prompt", "top_k_source", "remaining_defaults"}
        _need(set(request) == required_recipe | {"schema", "source_selection_request_sha256", "source_sha256", "source_bytes",
                                                  "offset_unit", "selected_keyword_ids", "selected_source_evidence"}, "Incomplete/unknown full summary recipe fields.")
        _need(all(isinstance(request[field], dict) for field in ("model", "runtime", "sampler", "generation", "server")), "Invalid recipe objects.")
        _need(isinstance(request["model"].get("sha256"), str) and SHA.fullmatch(request["model"]["sha256"]) is not None
              and request["runtime"].get("engine") == "llama.cpp", "Invalid model/runtime recipe identity.")
        model, runtime = request["model"], request["runtime"]
        _need(set(model) == {"repo", "revision", "file", "sha256", "size_bytes", "license"}
              and all(isinstance(model[field], str) and model[field] for field in ("repo", "revision", "file", "license"))
              and re.fullmatch(r"[0-9a-f]{40}", model["revision"]) is not None
              and type(model["size_bytes"]) is int and model["size_bytes"] > 0, "Incomplete model identity/version/checksum recipe.")
        _need(set(runtime) == {"engine", "version", "revision", "image", "license"}
              and all(isinstance(runtime[field], str) and runtime[field] for field in runtime)
              and re.fullmatch(r"[0-9a-f]{40}", runtime["revision"]) is not None
              and re.search(r"@sha256:[0-9a-f]{64}\Z", runtime["image"]) is not None, "Incomplete pinned runtime recipe.")
        _need(all(isinstance(request[field], str) and request[field] for field in ("prompt_version", "system_prompt", "top_k_source", "remaining_defaults")), "Incomplete prompt recipe.")
        sampler = request["sampler"]
        _need(set(sampler) == {"temperature", "seed", "top_p", "repeat_penalty", "top_k"}, "Incomplete sampler recipe.")
        _need(type(sampler.get("top_k")) is int and sampler["top_k"] >= 0 and type(sampler.get("seed")) is int
              and all(type(sampler.get(field)) in (int, float) for field in ("temperature", "top_p", "repeat_penalty")), "Invalid sampler recipe.")
        _need(sampler["temperature"] >= 0 and 0 < sampler["top_p"] <= 1 and sampler["repeat_penalty"] > 0, "Sampler parameters outside supported limits.")
        generation, server = request["generation"], request["server"]
        _need(set(generation) == {"max_tokens", "stream", "cache_prompt"}
              and set(server) == {"context_size", "parallel", "threads", "batch_size", "gpu_layers"}
              and all(type(server[field]) is int and server[field] > 0 for field in ("context_size", "parallel", "threads", "batch_size"))
              and type(server["gpu_layers"]) is int and server["gpu_layers"] >= 0, "Incomplete generation/server recipe.")
        _need(type(generation.get("max_tokens")) is int and generation["max_tokens"] > 0
              and generation.get("stream") is False and generation.get("cache_prompt") is False
              and type(server.get("context_size")) is int and server["context_size"] > 0, "Invalid generation recipe.")
        if expected_summary_request is not None:
            _need(canonical_request(request) == canonical_request(_copy(expected_summary_request)), "Artifact does not match the exact expected summary recipe.")
        _need(isinstance(receipt, dict) and set(receipt) == {"schema", "status", "execution_mode", "model_inference_performed",
              "generated_text", "confidence", "confidence_reason", "usage", "finish_reason", "truncated", "elapsed_ms",
              "generated_at", "runtime_observed", "generated_text_sha256", "source_selection_request_sha256",
              "summary_request_sha256", "wire_request_sha256", "determinism_note", "verification", "observation_log"},
              "Unknown summary receipt fields; extra metadata must not be published.")
        _need(receipt["confidence_reason"] == "Uncalibrated model output; no probability of correctness is claimed."
              and receipt["verification"] == "Records one owned local llama.cpp execution. Hashes bind content, not truth or producer authenticity."
              and receipt["determinism_note"] == "Fixed sampler settings do not guarantee deterministic generation across machines, models or runtimes. This is one recorded build execution, not a quality, confidence or reproducibility guarantee.",
              "Unexpected receipt explanatory metadata.")
        _need(receipt["schema"] == "lab-summary-receipt/1" and receipt["status"] == "generated"
              and receipt["execution_mode"] == "build_time_llama_cpp" and receipt["model_inference_performed"] is True
              and receipt["confidence"] is None, "Artifact does not contain a successful build generation receipt.")
        _need(receipt["source_selection_request_sha256"] == selection_key and receipt["summary_request_sha256"] == key, "Receipt request checksum mismatch.")
        text = receipt["generated_text"]
        _need(isinstance(text, str) and bool(text.strip()) and len(text.encode("utf-8")) <= 8192
              and _digest(text.encode("utf-8")) == receipt["generated_text_sha256"], "Generated text checksum/size mismatch.")
        usage = receipt["usage"]
        _need(isinstance(usage, dict) and set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
              and all(type(value) is int and value > 0 for value in usage.values()), "Invalid observed token usage.")
        _need(usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
              and usage["total_tokens"] <= server["context_size"] and usage["completion_tokens"] <= generation["max_tokens"], "Observed usage violates recipe limits.")
        _need(type(receipt["elapsed_ms"]) is int and receipt["elapsed_ms"] >= 0
              and receipt["finish_reason"] in {"stop", "length"}
              and receipt["truncated"] is (receipt["finish_reason"] == "length"), "Invalid generation timing/finish receipt.")
        _need(datetime.fromisoformat(receipt["generated_at"]).tzinfo is not None, "Generation timestamp needs a timezone.")
        runtime_observed = receipt["runtime_observed"]
        _need(isinstance(runtime_observed, dict) and set(runtime_observed) == {"version_output", "binary_sha256", "verification", "observed_default_top_k"},
              "Unknown runtime observation fields; extra metadata must not be published.")
        _need(isinstance(runtime_observed["version_output"], str) and 0 < len(runtime_observed["version_output"]) <= 4096
              and isinstance(runtime_observed["binary_sha256"], str) and SHA.fullmatch(runtime_observed["binary_sha256"]) is not None
              and type(runtime_observed["observed_default_top_k"]) is int and runtime_observed["observed_default_top_k"] == sampler["top_k"]
              and runtime_observed["verification"] == "Owned local process; version/revision checked and executable bytes hashed. The version string alone is not producer authentication.",
              "Invalid runtime observation.")
        version = runtime["version"].removeprefix("b")
        _need(re.search(r"\bversion:\s*(?:b)?" + re.escape(version) + r"\s*\(" + re.escape(runtime["revision"][:7]) + r"[0-9a-f]*\)", runtime_observed["version_output"]) is not None,
              "Runtime observation version/revision mismatch.")
        build = artifact["build"]
        _need(isinstance(build, dict) and set(build) == {"source_sha", "build_run", "build_attempt"}
              and re.fullmatch(r"[0-9a-f]{40}", build.get("source_sha", "")) is not None
              and all(build[field] is None or (isinstance(build[field], str) and re.fullmatch(r"[0-9]{1,32}", build[field]) is not None)
                      for field in ("build_run", "build_attempt")), "Unknown/invalid build metadata.")
        # Reconstruct the wire request with the stored complete recipe, not current constants.
        body = {"model": "lab-public-summary-model", "messages": [
            {"role": "system", "content": request["system_prompt"]},
            {"role": "user", "content": "Selected evidence (JSON data only):\n" + canonical_request([
                {"sentence_id": item["id"], "text": item["text"]} for item in request["selected_source_evidence"]])},
        ], **sampler, **generation}
        _need(receipt["wire_request_sha256"] == _hash(body), "Wire request checksum mismatch.")
        observed = _artifact_log(artifact, log_bytes)
        _need(observed.get("schema") == "lab-summary-observation/1" and observed.get("event") == "portable/summary_generation"
              and observed.get("model_inference_performed") is True, "Invalid model observation event.")
        for field in ("source_selection_request_sha256", "summary_request_sha256", "wire_request_sha256", "generated_text_sha256", "usage", "elapsed_ms", "finish_reason", "generated_at"):
            _need(_same(observed.get(field), receipt[field]), "Observation and receipt do not match.")
    except (KeyError, TypeError, AttributeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, CorpusError):
            raise
        raise CorpusError("Malformed summary artifact/receipt.") from exc
    return artifact


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (canonical_request(value) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".corpus-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SummaryCorpus:
    """On-disk keyed index; each lookup loads one bounded row, never the corpus.

    Writers are serialized with an exclusive lock; stale locks are not guessed
    away. Append-only data and immutable references make interrupted writes
    detectable by check(). Existing keys are never overwritten.
    """
    def __init__(self, root: str | Path):
        self.root = Path(root)
        _need(not self.root.is_symlink(), "Corpus root must not be a symlink.")

    def _path(self, *parts: str) -> Path:
        _need(not self.root.is_symlink(), "Corpus root must not be a symlink.")
        path = self.root
        for part in parts:
            path /= part
            _need(not path.is_symlink(), "Symlinks inside a corpus are not allowed.")
        return path

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self._path(".writer.lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CorpusError("Corpus writer lock exists; wait for the writer or inspect interrupted work.") from exc
        try:
            os.close(fd)
            yield
        finally:
            lock.unlink()

    def _ref_path(self, kind: str, key: str) -> Path:
        _need(kind in KINDS and isinstance(key, str) and SHA.fullmatch(key) is not None, "Invalid corpus kind/content key.")
        return self._path("index", kind, key[:2], f"{key}.json")

    def _reference(self, kind: str, key: str) -> dict[str, Any] | None:
        path = self._ref_path(kind, key)
        if not path.exists():
            return None
        ref = _parse(_read_file(path, 2048), limit=2048)
        _need(set(ref) == {"schema", "kind", "key", "shard", "offset", "length", "row_sha256"}
              and ref["schema"] == "lab-summary-corpus-reference/1" and ref["kind"] == kind and ref["key"] == key,
              "Invalid immutable corpus reference.")
        _need(isinstance(ref["shard"], str) and SHARD.fullmatch(ref["shard"]) is not None
              and type(ref["offset"]) is int and ref["offset"] >= 0 and type(ref["length"]) is int
              and 0 < ref["length"] <= MAX_RECORD_BYTES and ref["offset"] + ref["length"] <= MAX_SHARD_BYTES
              and isinstance(ref["row_sha256"], str) and SHA.fullmatch(ref["row_sha256"]) is not None, "Unsafe shard reference.")
        return ref

    @contextmanager
    def _open_shard(self, shard: str):
        _need(isinstance(shard, str) and SHARD.fullmatch(shard) is not None, "Invalid shard identity.")
        path = self._path("shards", f"{shard}.jsonl")
        if path.exists():
            _need(not self._path("archives", f"{shard}.zip").exists(), "Both shard and archive exist; inspect interrupted archiving.")
            _need(path.is_file() and path.stat().st_size <= MAX_SHARD_BYTES, "Invalid or oversized JSONL shard.")
            with path.open("rb") as handle:
                yield handle
            return
        archive = self._path("archives", f"{shard}.zip")
        _need(archive.is_file() and archive.stat().st_size <= MAX_ARCHIVE_BYTES, "Missing or oversized shard archive.")
        # Reject a huge central directory/ZIP64 before ZipFile allocates its entry list.
        with archive.open("rb") as handle:
            size = archive.stat().st_size
            handle.seek(max(0, size - 65557))
            tail = handle.read(65557)
            end = tail.rfind(b"PK\x05\x06")
            _need(end >= 0 and end + 22 <= len(tail), "Invalid ZIP end record.")
            _, disk, directory_disk, disk_count, count, directory_size, directory_offset, comment = struct.unpack("<4s4H2LH", tail[end:end + 22])
            _need(disk == directory_disk == 0 and disk_count == count == MAX_ARCHIVE_MEMBERS
                  and directory_size <= 4096 and directory_offset + directory_size == size - len(tail) + end
                  and end + 22 + comment == len(tail), "Unsafe ZIP entry count/directory/ZIP64 layout.")
        try:
            with zipfile.ZipFile(archive) as zipped:
                infos = zipped.infolist()
                _need(len(infos) == 1, "Archive must contain exactly one bounded JSONL shard.")
                member = infos[0]
                mode = member.external_attr >> 16
                _need(member.filename == f"{shard}.jsonl" and not member.is_dir()
                      and not stat.S_ISLNK(mode) and not member.flag_bits & 1
                      and member.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                      and 0 < member.file_size <= MAX_SHARD_BYTES
                      and 0 < member.compress_size <= MAX_ARCHIVE_BYTES
                      and member.file_size <= max(1, member.compress_size) * 1000, "Unsafe ZIP member/path/size/compression.")
                with zipped.open(member) as handle:
                    yield handle
        except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
            raise CorpusError("Corrupt shard archive.") from exc

    def _get(self, kind: str, key: str) -> dict[str, Any] | None:
        ref = self._reference(kind, key)
        if ref is None:
            return None
        with self._open_shard(ref["shard"]) as handle:
            if ref["offset"]:
                handle.seek(ref["offset"] - 1)
                _need(handle.read(1) == b"\n", "Reference is not aligned to a JSONL record.")
            raw = handle.read(ref["length"])
        _need(len(raw) == ref["length"] and raw.endswith(b"\n") and _digest(raw) == ref["row_sha256"], "Referenced JSONL row checksum/length mismatch.")
        row = _parse(raw)
        _need(set(row) == {"schema", "kind", "key", "value"} and row["schema"] == "lab-summary-corpus-record/1"
              and row["kind"] == kind and row["key"] == key and isinstance(row["value"], dict), "Referenced record identity mismatch.")
        return row["value"]

    def _append(self, kind: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
        # Caller holds the writer lock. Return existing immutable records verbatim.
        prior = self._get(kind, key)
        if prior is not None:
            return prior
        row = {"schema": "lab-summary-corpus-record/1", "kind": kind, "key": key, "value": _copy(value)}
        raw = (canonical_request(row) + "\n").encode("utf-8")
        _need(len(raw) <= MAX_RECORD_BYTES, "Wrapped record exceeds its JSONL byte budget.")
        active_path = self._path("active.json")
        active = _parse(_read_file(active_path, 2048), limit=2048) if active_path.exists() else None
        if active is not None:
            _need(set(active) == {"schema", "shard", "bytes", "records"} and active["schema"] == "lab-summary-corpus-active/1"
                  and isinstance(active["shard"], str) and SHARD.fullmatch(active["shard"]) is not None
                  and type(active["bytes"]) is int and 0 < active["bytes"] <= MAX_SHARD_BYTES
                  and type(active["records"]) is int and 0 < active["records"] <= MAX_SHARD_RECORDS, "Invalid active shard state.")
            path = self._path("shards", f"{active['shard']}.jsonl")
            _need(path.is_file() and path.stat().st_size == active["bytes"], "Interrupted or altered active shard; run check before writing.")
            _need(self._check_shard(active["shard"]) == active["records"], "Active shard record count mismatch.")
        if active is None or active["bytes"] + len(raw) > MAX_SHARD_BYTES or active["records"] >= MAX_SHARD_RECORDS:
            active = {"schema": "lab-summary-corpus-active/1", "shard": uuid.uuid4().hex, "bytes": 0, "records": 0}
        path = self._path("shards", f"{active['shard']}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        offset = active["bytes"]
        # The debug-log append_jsonl helper skips corrupt rows on read and cannot
        # provide strict byte offsets/fsync, so corpus persistence is separate.
        with path.open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        active["bytes"] += len(raw)
        active["records"] += 1
        _atomic_json(active_path, active)
        ref = {"schema": "lab-summary-corpus-reference/1", "kind": kind, "key": key,
               "shard": active["shard"], "offset": offset, "length": len(raw), "row_sha256": _digest(raw)}
        _atomic_json(self._ref_path(kind, key), ref)
        return value

    def ingest_request(self, request: dict[str, Any], request_id: str, *, review: dict[str, Any] | None = None) -> dict[str, Any]:
        case = make_case(request, request_id, review=review)
        with self._locked():
            prior = self._get("cases", case["source_selection_request_sha256"])
            if prior is not None:
                prior = validate_case(prior)
                _need(prior["review"] == case["review"], "Identical case already has a different immutable review; use a separately reviewed export.")
                return prior
            return self._append("cases", case["source_selection_request_sha256"], case)

    def get_case(self, key: str) -> dict[str, Any] | None:
        case = self._get("cases", key)
        if case is None:
            return None
        checked = validate_case(case)
        _need(checked["source_selection_request_sha256"] == key, "Case content key mismatch.")
        return checked

    def _keys(self, kind: str) -> Iterator[str]:
        directory = self._path("index", kind)
        if not directory.exists():
            return
        with os.scandir(directory) as buckets:
            for bucket in buckets:
                _need(re.fullmatch(r"[0-9a-f]{2}", bucket.name) is not None and bucket.is_dir(follow_symlinks=False), "Unexpected corpus index bucket.")
                with os.scandir(bucket.path) as entries:
                    for entry in entries:
                        key = entry.name.removesuffix(".json")
                        _need(entry.name == f"{key}.json" and SHA.fullmatch(key) is not None and key.startswith(bucket.name)
                              and entry.is_file(follow_symlinks=False), "Unexpected corpus index entry.")
                        yield key

    def iter_cases(self) -> Iterator[dict[str, Any]]:
        for key in self._keys("cases"):
            yield self.get_case(key)

    def get_observation_log(self, key: str) -> bytes:
        value = self._get("logs", key)
        _need(value is not None and set(value) == {"schema", "sha256", "text"}
              and value["schema"] == "lab-summary-corpus-log/1" and value["sha256"] == key
              and isinstance(value["text"], str), "Missing/invalid stored observation log.")
        raw = value["text"].encode("utf-8")
        _need(0 < len(raw) <= MAX_LOG_BYTES and _digest(raw) == key, "Stored observation-log checksum mismatch.")
        for number, (_, _, row) in enumerate(_lines(io.BytesIO(raw), total_limit=MAX_LOG_BYTES), 1):
            _need(number <= MAX_LOG_LINES, "Too many observation-log records.")
            _validate_observation(row)
        return raw

    def put_result(self, artifact: dict[str, Any], observation_log_bytes: bytes) -> dict[str, Any]:
        artifact = validate_summary_artifact(artifact, log_bytes=observation_log_bytes)
        key = artifact["summary_request_sha256"]
        log_key = _digest(observation_log_bytes)
        with self._locked():
            prior = self.get_result(key)
            if prior is not None:
                _need(prior == artifact, "A different result already exists for this exact full recipe; immutable results cannot be replaced.")
                return {"schema": "lab-summary-corpus-result/1", "summary_request_sha256": key, "artifact": prior, "observation_log_sha256": log_key}
            self._append("logs", log_key, {"schema": "lab-summary-corpus-log/1", "sha256": log_key, "text": observation_log_bytes.decode("utf-8")})
            return self._append("results", key, {"schema": "lab-summary-corpus-result/1", "summary_request_sha256": key,
                                                "artifact": artifact, "observation_log_sha256": log_key})

    def get_result(self, key: str, *, expected_request: dict[str, Any] | None = None,
                   expected_selection: dict[str, Any] | None = None) -> dict[str, Any] | None:
        value = self._get("results", key)
        if value is None:
            return None
        _need(set(value) == {"schema", "summary_request_sha256", "artifact", "observation_log_sha256"}
              and value["schema"] == "lab-summary-corpus-result/1" and value["summary_request_sha256"] == key, "Invalid result record.")
        artifact = validate_summary_artifact(value["artifact"], log_bytes=self.get_observation_log(value["observation_log_sha256"]),
                                            expected_summary_request=expected_request)
        _need(artifact["summary_request_sha256"] == key and artifact["receipt"]["observation_log"]["sha256"] == value["observation_log_sha256"], "Result content key mismatch.")
        if expected_selection is not None:
            _need(artifact["source_selection_request"] == expected_selection, "Result selection mismatch.")
        return artifact

    def _check_shard(self, shard: str) -> int:
        count = 0
        with self._open_shard(shard) as handle:
            for offset, raw, row in _lines(handle, total_limit=MAX_SHARD_BYTES):
                count += 1
                _need(count <= MAX_SHARD_RECORDS, "Shard has too many records.")
                _need(set(row) == {"schema", "kind", "key", "value"} and row["schema"] == "lab-summary-corpus-record/1", "Invalid shard record schema.")
                ref = self._reference(row["kind"], row["key"])
                _need(ref is not None and ref["shard"] == shard and ref["offset"] == offset and ref["length"] == len(raw)
                      and ref["row_sha256"] == _digest(raw), "Orphaned, duplicate, or mismatched shard reference.")
        _need(count > 0, "Empty shard.")
        return count

    def archive(self, *, max_shards: int = 16) -> list[str]:
        """Seal and archive up to max_shards locally; never extract ZIP members."""
        _need(type(max_shards) is int and 1 <= max_shards <= 256, "Archive batch must be 1-256 shards.")
        done = []
        with self._locked():
            active = self._path("active.json")
            if active.exists():
                state = _parse(_read_file(active, 2048), limit=2048)
                self._check_shard(state.get("shard"))
                active.unlink()
            directory = self._path("shards")
            if not directory.exists():
                return done
            with os.scandir(directory) as entries:
                for entry in entries:
                    shard = entry.name.removesuffix(".jsonl")
                    _need(entry.name == f"{shard}.jsonl" and SHARD.fullmatch(shard) is not None and entry.is_file(follow_symlinks=False), "Unexpected shard file.")
                    self._check_shard(shard)
                    target = self._path("archives", f"{shard}.zip")
                    _need(not target.exists(), "Archive already exists; inspect interrupted archiving before retrying.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=target.parent)
                    os.close(fd)
                    try:
                        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=False) as zipped:
                            zipped.write(entry.path, arcname=f"{shard}.jsonl")
                        # Bounded streaming equality verifies the exact shard before removal.
                        with zipfile.ZipFile(temporary) as zipped, zipped.open(f"{shard}.jsonl") as restored, open(entry.path, "rb") as original:
                            while chunk := original.read(65536):
                                _need(restored.read(len(chunk)) == chunk, "Archive verification failed.")
                            _need(restored.read(1) == b"", "Archive contains extra bytes.")
                        os.replace(temporary, target)
                        os.unlink(entry.path)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                    done.append(shard)
                    if len(done) == max_shards:
                        break
        return done

    def check(self) -> dict[str, int]:
        """Full streaming audit, including corruption outside any requested key."""
        counts = {"cases": 0, "results": 0, "logs": 0, "shards": 0}
        active_path = self._path("active.json")
        if active_path.exists():
            active = _parse(_read_file(active_path, 2048), limit=2048)
            _need(set(active) == {"schema", "shard", "bytes", "records"}
                  and active["schema"] == "lab-summary-corpus-active/1"
                  and type(active["bytes"]) is int and 0 < active["bytes"] <= MAX_SHARD_BYTES
                  and type(active["records"]) is int and 0 < active["records"] <= MAX_SHARD_RECORDS, "Invalid active shard state.")
            _need(self._check_shard(active["shard"]) == active["records"], "Active shard record count mismatch.")
            _need(self._path("shards", f"{active['shard']}.jsonl").stat().st_size == active["bytes"], "Active shard byte count mismatch.")
        for kind in KINDS:
            for key in self._keys(kind):
                if kind == "cases":
                    self.get_case(key)
                elif kind == "results":
                    self.get_result(key)
                else:
                    self.get_observation_log(key)
                counts[kind] += 1
        for directory, suffix in (("shards", ".jsonl"), ("archives", ".zip")):
            path = self._path(directory)
            if not path.exists():
                continue
            with os.scandir(path) as entries:
                for entry in entries:
                    shard = entry.name.removesuffix(suffix)
                    _need(entry.name == shard + suffix and SHARD.fullmatch(shard) is not None and entry.is_file(follow_symlinks=False), "Unexpected corpus shard/archive entry.")
                    self._check_shard(shard)
                    counts["shards"] += 1
        return counts


def summarize_case_coverage(cases: Iterator[dict[str, Any]]) -> dict[str, Any]:
    """Count unique validated cases and objective coverage without storing text.

    Deduplication and distinct source counts use a temporary disk index. Bucket
    counts cover unique cases only; no topic, quality, or accuracy is inferred.
    Generation/reuse/failure counts belong to the actual execution report.
    """
    keyword_buckets = {"0": 0, "1": 0, "2-4": 0, "5-8": 0, "9+": 0}
    sentence_buckets = {"1": 0, "2-4": 0, "5-8": 0, "9+": 0}
    discovered = unique = 0
    def bucket(count: int) -> str:
        return str(count) if count <= 1 else "2-4" if count <= 4 else "5-8" if count <= 8 else "9+"
    with tempfile.TemporaryDirectory(prefix="lab-corpus-coverage-") as temporary:
        with closing(sqlite3.connect(str(Path(temporary) / "keys.sqlite3"))) as db:
            db.execute("PRAGMA cache_size = -1024")
            db.execute("PRAGMA temp_store = FILE")
            db.execute("CREATE TABLE cases (key TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE sources (key TEXT PRIMARY KEY)")
            for value in cases:
                case = validate_case(value)
                discovered += 1
                added = db.execute("INSERT OR IGNORE INTO cases VALUES (?)", (case["source_selection_request_sha256"],)).rowcount
                if not added:
                    continue
                unique += 1
                db.execute("INSERT OR IGNORE INTO sources VALUES (?)", (case["source_sha256"],))
                keyword_buckets[bucket(len(case["source_selection_request"]["input"]["keywords"]))] += 1
                sentence_buckets[bucket(len(case["selected_source_evidence"]))] += 1
            source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    return {"discovered_cases": discovered, "unique_cases": unique,
            "duplicate_cases": discovered - unique, "distinct_sources": source_count,
            "keyword_count_buckets": keyword_buckets,
            "selected_sentence_count_buckets": sentence_buckets}


def iter_reviewed_requests(path: str | Path) -> Iterator[dict[str, Any]]:
    path = Path(path)
    cases = SummaryCorpus(path).iter_cases() if path.is_dir() else (validate_case(row) for row in iter_jsonl(path))
    for case in cases:
        _need(case["review"] is not None and case["review"]["classification"] == "reviewed_public", "Public builds require explicit reviewed_public cases; private imports are not publishable.")
        yield case


def load_reviewed_corpus(path: str | Path, *, max_choices: int = 16, max_sources: int = 4) -> list[dict[str, Any]]:
    """Small manifest projection only; the backing corpus stays streaming/local."""
    sources = {}
    seen = set()
    for case in iter_reviewed_requests(path):
        key = case["source_selection_request_sha256"]
        if key in seen:
            continue
        _need(len(seen) < max_choices, "Reviewed corpus exceeds the build choice budget; select a smaller explicit batch.")
        seen.add(key)
        source_key = case["source_sha256"]
        selection = case["source_selection_request"]
        _need(case["source_bytes"] <= 4096, "Public build source exceeds 4 KiB.")
        if source_key not in sources:
            _need(len(sources) < max_sources, "Reviewed corpus exceeds the build source budget.")
            sources[source_key] = {"id": source_key, "title": f"Reviewed source {source_key[:12]}", "text": selection["input"]["text"], "source_sha256": source_key, "choices": []}
        sources[source_key]["choices"].append({"id": case["request_id"], "label": case["request_id"],
                                              "source_selection_request": selection, "source_selection_request_sha256": key})
    _need(bool(seen), "Reviewed corpus contains no cases.")
    return list(sources.values())


def load_reviewed_corpus_window(path: str | Path, *, case_offset: int = 0,
                                case_limit: int = 16, max_sources: int = 64) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicated, stable content-key pagination with bounded memory.

    A disposable on-disk SQLite index orders keys and keeps source records off
    the Python heap. Only the requested window (at most 256 cases) is returned.
    Adding cases can shift offsets; corpus_sha256 identifies the key snapshot.
    """
    _need(type(case_offset) is int and 0 <= case_offset <= 2**31 - 1, "Invalid case_offset.")
    _need(type(case_limit) is int and 1 <= case_limit <= 256, "case_limit must be 1-256.")
    _need(type(max_sources) is int and 1 <= max_sources <= 256, "Invalid max_sources.")
    with tempfile.TemporaryDirectory(prefix="lab-corpus-window-") as temporary:
        with closing(sqlite3.connect(str(Path(temporary) / "keys.sqlite3"))) as db:
            db.execute("PRAGMA cache_size = -1024")
            db.execute("PRAGMA temp_store = FILE")
            db.execute("CREATE TABLE cases (key TEXT PRIMARY KEY, record TEXT NOT NULL)")
            for case in iter_reviewed_requests(path):
                db.execute("INSERT OR IGNORE INTO cases VALUES (?, ?)",
                           (case["source_selection_request_sha256"], canonical_request(case)))
            db.commit()
            total = db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            _need(total > 0, "Reviewed corpus contains no cases.")
            _need(case_offset < total, "case_offset is outside this corpus snapshot.")
            fingerprint = hashlib.sha256()
            for (key,) in db.execute("SELECT key FROM cases ORDER BY key"):
                fingerprint.update((key + "\n").encode("ascii"))
            sources = {}
            returned = 0
            for (record,) in db.execute("SELECT record FROM cases ORDER BY key LIMIT ? OFFSET ?", (case_limit, case_offset)):
                case = _parse(record.encode("utf-8"))
                selection = case["source_selection_request"]
                source_key = case["source_sha256"]
                _need(case["source_bytes"] <= 4096, "Public build source exceeds 4 KiB.")
                if source_key not in sources:
                    _need(len(sources) < max_sources, "Reviewed corpus window exceeds the build source budget.")
                    sources[source_key] = {"id": source_key, "title": f"Reviewed source {source_key[:12]}",
                        "text": selection["input"]["text"], "source_sha256": source_key, "choices": []}
                sources[source_key]["choices"].append({"id": case["request_id"], "label": case["request_id"],
                    "source_selection_request": selection, "source_selection_request_sha256": case["source_selection_request_sha256"]})
                returned += 1
            has_more = case_offset + returned < total
            return list(sources.values()), {"offset": case_offset, "limit": case_limit, "returned_cases": returned,
                "next_offset": case_offset + returned if has_more else None, "has_more": has_more,
                "total_cases": total, "corpus_sha256": fingerprint.hexdigest(),
                "ordering": "source_selection_request_sha256"}


def load_verified_cached_artifact(previous_dir: str | Path, summary_request: dict[str, Any],
                                  selection: dict[str, Any]) -> dict[str, Any] | None:
    previous = Path(previous_dir)
    key = _hash(summary_request)
    path = previous / "summaries" / f"{key}.json"
    _need(not previous.is_symlink() and not (previous / "summaries").is_symlink(), "Unsafe previous-artifact directory.")
    if not path.exists():
        return None
    artifact = _parse(_read_file(path, MAX_RECORD_BYTES))
    try:
        log = artifact["receipt"]["observation_log"]
        log_key = log["sha256"]
        _need(isinstance(log_key, str) and SHA.fullmatch(log_key) is not None
              and log["path"] in {"summary-build.jsonl", f"summary-logs/{log_key}.jsonl"}, "Unsafe cached observation-log path.")
        _need(not (previous / "summary-logs").is_symlink(), "Unsafe cached log directory.")
        raw = _read_file(previous / log["path"], MAX_LOG_BYTES)
    except (KeyError, TypeError) as exc:
        raise CorpusError("Invalid cached observation-log reference.") from exc
    artifact = validate_summary_artifact(artifact, log_bytes=raw, expected_summary_request=summary_request)
    _need(artifact["source_selection_request"] == selection, "Cached artifact selection mismatch.")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Import one browser source_selection JSON request, locally only")
    ingest.add_argument("request", type=Path)
    ingest.add_argument("--request-id", required=True)
    ingest.add_argument("--reviewed-public-by", help="Explicit assertion that the input has been reviewed for publication")
    ingest.add_argument("--reviewed-at", help="ISO timestamp with timezone, required with --reviewed-public-by")
    imported = commands.add_parser("import-jsonl", help="Import validated corpus case records, preserving review status")
    imported.add_argument("path", type=Path)
    exported = commands.add_parser("export-jsonl", help="Export case records to a NEW local file, never a website")
    exported.add_argument("path", type=Path)
    commands.add_parser("archive", help="Archive at most 16 local shards; repeat for remaining shards")
    commands.add_parser("check", help="Strict full streaming audit")
    args = parser.parse_args()
    corpus = SummaryCorpus(args.corpus)
    try:
        if args.command == "ingest":
            _need(bool(args.reviewed_public_by) == bool(args.reviewed_at), "Supply both review fields or neither.")
            review = {"classification": "reviewed_public", "reviewed_by": args.reviewed_public_by, "reviewed_at": args.reviewed_at} if args.reviewed_public_by else None
            case = corpus.ingest_request(_parse(_read_file(args.request, MAX_RECORD_BYTES)), args.request_id, review=review)
            result = {"case_sha256": case["source_selection_request_sha256"], "review": case["review"], "published": False}
        elif args.command == "import-jsonl":
            count = 0
            for case in iter_jsonl(args.path):
                case = validate_case(case)
                corpus.ingest_request(case["source_selection_request"], case["request_id"], review=case["review"])
                count += 1
            result = {"validated_input_rows": count, "published": False}
        elif args.command == "export-jsonl":
            count = 0
            with args.path.open("xb") as handle:
                for case in corpus.iter_cases():
                    handle.write((canonical_request(case) + "\n").encode("utf-8"))
                    count += 1
            result = {"exported_cases": count, "published": False}
        elif args.command == "archive":
            result = {"archived_shards": corpus.archive(), "published": False}
        else:
            result = corpus.check()
    except (CorpusError, OSError) as exc:
        parser.exit(2, f"Corpus operation failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
