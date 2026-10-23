"""Bounded, explicit non-Pages corpus handoff and a two-process build check.

No downloads, network API, automatic artifact retrieval or upload. Handoff data
must all be reviewed public: a CI artifact in a public repository is not private.
The check uses two fixed synthetic cases and the already prepared local model.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import zipfile
import zlib
import subprocess
import sys
from typing import Any

from mcp_toolcall_lab.portable_execution import canonical_request, execute
from mcp_toolcall_lab.portable_summary_build import build_summaries, declared_sources, prepare_summary_request
from mcp_toolcall_lab.portable_summary_corpus import (SummaryCorpus, iter_jsonl, make_case, validate_case,
    iter_reviewed_requests, load_reviewed_corpus_window, load_verified_cached_artifact, summarize_case_coverage)

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 4096
MAX_HANDOFF_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 + 65536
MAX_ADDITIONS_BYTES = 4 * 1024 * 1024
MAX_CASES = 512
FILE_PATTERNS = [
    re.compile(r"active\.json\Z"),
    re.compile(r"shards/[0-9a-f]{32}\.jsonl\Z"),
    re.compile(r"archives/[0-9a-f]{32}\.zip\Z"),
    re.compile(r"index/(?:cases|results|logs)/[0-9a-f]{2}/[0-9a-f]{64}\.json\Z"),
]
DIRECTORY_PATTERN = re.compile(r"(?:shards|archives|index(?:/(?:cases|results|logs)(?:/[0-9a-f]{2})?)?)\Z")
REVIEW = {"classification": "reviewed_public", "reviewed_by": "repository-synthetic-fixture", "reviewed_at": "2026-10-23T00:00:00+00:00"}
SYNTHETIC_TEXTS = (
    "The fictional archive stores a blue notebook. The notebook records weekly observations.",
    "A separate fictional archive stores a green notebook. Volunteers catalogue new observations after each visit.",
)


class HandoffError(ValueError):
    """An input is not a bounded, complete reviewed-public corpus handoff."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise HandoffError(message)


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _fresh_output(path: Path, protected: list[Path | None]) -> Path:
    _need(not path.is_symlink(), "Output must not be a symlink.")
    _need(not path.exists() or (path.is_dir() and not any(path.iterdir())), "Output must be a fresh empty directory.")
    resolved = path.resolve()
    for source in protected:
        if source is None:
            continue
        other = source.resolve()
        _need(not (other == resolved or resolved in other.parents or other in resolved.parents),
              "Handoff output and input/model/runtime paths must not overlap, including symlink targets.")
    return path


def _snapshot_files(root: Path) -> list[dict[str, Any]]:
    """Allowlist every file before copying; never transfer arbitrary extra data."""
    _need(root.is_dir() and not root.is_symlink(), "Input corpus must be a regular directory containing its complete indexes/shards.")
    files = []
    total = 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in names:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            _need(not path.is_symlink() and DIRECTORY_PATTERN.fullmatch(relative) is not None,
                  "Unexpected or symlinked corpus directory; refusing wholesale transfer.")
        for name in filenames:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            _need(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)
                  and any(pattern.fullmatch(relative) for pattern in FILE_PATTERNS),
                  "Unexpected corpus file, writer lock, or symlink; handoff contains only validated storage files.")
            size = path.stat().st_size
            total += size
            _need(len(files) < MAX_FILES and total <= MAX_HANDOFF_BYTES, "Corpus handoff exceeds 4096 files or 64 MiB; split the explicitly selected corpus.")
            files.append({"path": relative, "size_bytes": size, "sha256": _sha(path)})
    return sorted(files, key=lambda item: item["path"])


def _audit_archive_metadata(path: Path) -> None:
    """Reject unvalidated ZIP side channels before copying archive bytes.

    The store validates JSONL records. Here only the ordinary one-member layout
    emitted by SummaryCorpus.archive is eligible for publication handoff.
    """
    with path.open("rb") as handle:
        raw = handle.read(MAX_ARCHIVE_BYTES + 1)
    _need(len(raw) <= MAX_ARCHIVE_BYTES, "ZIP archive exceeds its bounded handoff budget.")
    _need(len(raw) >= 22 and raw[-22:-18] == b"PK\x05\x06", "ZIP comments/trailing bytes are not eligible for handoff.")
    _, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack("<4s4H2LH", raw[-22:])
    _need(disk == directory_disk == comment == 0 and disk_count == count == 1
          and size == 84 and offset + size == len(raw) - 22,
          "ZIP layout/metadata is not eligible for handoff.")
    # Construct ZipFile only after fixed-size central-directory preflight, and
    # only over the exact bounded bytes checked above (no filesystem reread).
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        _need(len(members) == 1 and not archive.comment, "ZIP archive metadata is not eligible for handoff.")
        member = members[0]
        name = member.filename.encode("ascii", errors="strict")
        _need(not member.comment and not member.extra and member.header_offset == 0
              and member.flag_bits == 0 and len(name) == 38 and name == (path.stem + ".jsonl").encode("ascii"),
              "ZIP member metadata/preamble is not eligible for handoff.")
        _need(len(raw) >= 30 and raw[:4] == b"PK\x03\x04", "ZIP local header/preamble is not eligible for handoff.")
        local = struct.unpack("<4s5H3L2H", raw[:30])
        _need(local[2] == 0 and local[-2] == len(name) and local[-1] == 0
              and raw[30:30 + len(name)] == name
              and local[3] == member.compress_type and local[6] == member.CRC
              and local[7] == member.compress_size and local[8] == member.file_size,
              "ZIP local header metadata is not eligible for handoff.")
        start = 30 + len(name)
        _need(start + member.compress_size == offset and size == 46 + len(name)
              and raw[offset:offset + 4] == b"PK\x01\x02"
              and raw[offset + 46:offset + size] == name,
              "ZIP preamble/gaps/extra metadata is not eligible for handoff.")
        central = struct.unpack("<4s6H3L5H2L", raw[offset:offset + 46])
        _need(central[10] == len(name) and central[11] == central[12] == central[13] == central[-1] == 0,
              "ZIP central metadata is not eligible for handoff.")
        if member.compress_type == zipfile.ZIP_DEFLATED:
            decoder = zlib.decompressobj(-15)
            decoded = decoder.decompress(raw[start:offset], 8 * 1024 * 1024 + 1)
            _need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
                  and len(decoded) == member.file_size, "ZIP compressed trailing data is not eligible for handoff.")
        else:
            _need(member.compress_type == zipfile.ZIP_STORED and member.compress_size == member.file_size,
                  "Unsupported ZIP compression for handoff.")


def audit_handoff(root: Path) -> dict[str, Any]:
    files = _snapshot_files(root)
    raw_shards = {Path(entry["path"]).stem for entry in files if entry["path"].startswith("shards/")}
    archived_shards = {Path(entry["path"]).stem for entry in files if entry["path"].startswith("archives/")}
    _need(not raw_shards & archived_shards, "Both raw and archived copies of a shard exist; refusing ambiguous handoff.")
    for entry in files:
        if entry["path"].startswith("archives/"):
            try:
                _audit_archive_metadata(root / entry["path"])
            except (zipfile.BadZipFile, zlib.error, struct.error, UnicodeError) as exc:
                raise HandoffError("Malformed ZIP metadata/compression; handoff rejected.") from exc
    store = SummaryCorpus(root)
    counts = store.check()
    _need(0 < counts["cases"] <= MAX_CASES, "Handoff requires 1..512 reviewed-public cases.")
    reviewed = set()
    for case in store.iter_cases():
        _need(case["review"] is not None and case["review"]["classification"] == "reviewed_public",
              "Unreviewed/private cases are not eligible for a non-Pages CI artifact.")
        reviewed.add(case["source_selection_request_sha256"])
    for entry in files:
        if entry["path"].startswith("index/results/"):
            artifact = store.get_result(Path(entry["path"]).stem)
            _need(artifact is not None and artifact["source_selection_request_sha256"] in reviewed,
                  "A stored result has no matching reviewed-public case; refusing transfer.")
    return {"counts": counts, "files": files,
            "sha256": hashlib.sha256(canonical_request(files).encode("utf-8")).hexdigest(),
            "size_bytes": sum(item["size_bytes"] for item in files)}


def _copy_snapshot(source: Path, target: Path, snapshot: dict[str, Any]) -> None:
    target.mkdir(parents=True)
    for entry in snapshot["files"]:
        src = source / entry["path"]
        dest = target / entry["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        _need(dest.stat().st_size == entry["size_bytes"] and _sha(dest) == entry["sha256"],
              "Input changed during handoff copy; keep corpus writers stopped and retry into a fresh directory.")
    copied = audit_handoff(target)
    _need(copied["sha256"] == snapshot["sha256"], "Copied corpus does not match the audited input snapshot.")


def _reviewed_additions(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    _need(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ADDITIONS_BYTES,
          "Reviewed additions must be a regular JSONL file no larger than 4 MiB.")
    records = []
    for row in iter_jsonl(path):
        case = validate_case(row)
        _need(case["review"] is not None and case["review"]["classification"] == "reviewed_public",
              "Every addition requires an explicit reviewed_public declaration.")
        _need(len(records) < 256, "At most 256 reviewed additions are accepted in one handoff round.")
        records.append(case)
    _need(bool(records), "Reviewed additions file is empty.")
    return records


def run_round(*, out: Path, source_sha: str, input_corpus: Path | None = None,
              append_reviewed_jsonl: Path | None = None, model: Path | None = None,
              llama_server: Path | None = None, case_offset: int = 0, case_limit: int = 16) -> dict[str, Any]:
    """Restore an explicit snapshot, append reviewed cases, build and re-archive.

    The input is never edited. out/corpus is a complete handoff, not a model cache
    or a Pages directory. out/execution alone is the selected static projection.
    """
    _need(input_corpus is not None or append_reviewed_jsonl is not None,
          "Select an input corpus and/or an explicit reviewed additions JSONL file.")
    _need(re.fullmatch(r"[0-9a-f]{40}", source_sha or "") is not None, "source_sha must be an exact 40-character source commit.")
    _need((model is None) == (llama_server is None), "Supply both existing model/runtime paths or neither.")
    _need(type(case_offset) is int and case_offset >= 0 and type(case_limit) is int and 1 <= case_limit <= 256,
          "Use a nonnegative case offset and a case limit of 1..256.")
    out = _fresh_output(Path(out), [input_corpus, append_reviewed_jsonl, model, llama_server])
    snapshot = audit_handoff(input_corpus) if input_corpus is not None else None
    additions = _reviewed_additions(append_reviewed_jsonl)
    prior_cases = snapshot["counts"]["cases"] if snapshot else 0
    addition_keys = {case["source_selection_request_sha256"] for case in additions}
    prior_keys = {case["source_selection_request_sha256"] for case in SummaryCorpus(input_corpus).iter_cases()} if snapshot else set()
    _need(len(prior_keys | addition_keys) <= MAX_CASES,
          "Combined handoff exceeds the 512-case bound.")
    out.mkdir(parents=True, exist_ok=True)
    corpus_path = out / "corpus"
    if snapshot is not None:
        _copy_snapshot(input_corpus, corpus_path, snapshot)
    else:
        corpus_path.mkdir()
    store = SummaryCorpus(corpus_path)
    for case in additions:
        store.ingest_request(case["source_selection_request"], case["request_id"], review=case["review"])
    after_ingest = audit_handoff(corpus_path)
    manifest = build_summaries(out / "execution", source_sha=source_sha, model_path=model,
        llama_server=llama_server, reviewed_corpus=corpus_path, result_cache=corpus_path,
        case_offset=case_offset, case_limit=case_limit)
    store.archive(max_shards=256)
    _need(not any((corpus_path / "shards").glob("*.jsonl")), "Handoff still contains unarchived shards; reduce the selected corpus.")
    completed = audit_handoff(corpus_path)
    report = {
        "schema": "lab-summary-handoff-round/1", "status": "complete", "worker_pid": os.getpid(),
        "source_sha": source_sha, "input_snapshot_sha256": snapshot["sha256"] if snapshot else None,
        "corpus_snapshot_sha256": completed["sha256"], "corpus_bytes": completed["size_bytes"],
        "counts_before": snapshot["counts"] if snapshot else {"cases": 0, "results": 0, "logs": 0, "shards": 0},
        "counts_after": completed["counts"], "distinct_cases_added": after_ingest["counts"]["cases"] - prior_cases,
        "submitted_additions": len(additions), "unique_additions": len(addition_keys),
        "duplicate_additions": len(additions) - len(addition_keys - prior_keys),
        "case_coverage": summarize_case_coverage(store.iter_cases()),
        "failed_generations": 0,  # Any failure raises; no success report is written.

        "new_generations": manifest["new_generations"], "reused_results": manifest["reused_results"],
        "model_inference_performed": manifest["model_inference_performed"],
        "generation_status": manifest["status"], "corpus_window": manifest["corpus_window"],
        "corpus_path": "corpus", "execution_path": "execution",
        "publication": "Non-Pages handoff; reviewed-public data only. This helper does not upload or publish.",
    }
    _write_json(out / "report.json", report)
    return report


def capture_execution(*, execution: Path, out: Path, source_sha: str,
                      reviewed_corpus: Path | None = None,
                      case_offset: int = 0, case_limit: int = 16) -> dict[str, Any]:
    """Capture actual selected public receipts without inferring review from them.

    No model invocation. Only the independently approved fixture or supplied
    reviewed case records select eligible results; arbitrary manifest text and
    unrelated files never enter the new handoff corpus.
    """
    _need(re.fullmatch(r"[0-9a-f]{40}", source_sha or "") is not None, "source_sha must be an exact 40-character source commit.")
    _need(execution.is_dir() and not execution.is_symlink(), "Execution input must be a regular local directory.")
    _need(type(case_offset) is int and case_offset >= 0 and type(case_limit) is int and 1 <= case_limit <= 256,
          "Use a nonnegative case offset and a case limit of 1..256.")
    out = _fresh_output(Path(out), [execution, reviewed_corpus])
    if reviewed_corpus is None:
        _need(case_offset == 0, "case_offset requires an explicitly reviewed corpus.")
        cases = [make_case(choice["source_selection_request"], choice["id"], review=REVIEW)
                 for source in declared_sources() for choice in source["choices"]]
    else:
        # Audit the complete selected input before any write, not just visible
        # window metadata. Preserve review identity; never promote private input.
        if reviewed_corpus.is_dir():
            audit_handoff(reviewed_corpus)
        else:
            _need(reviewed_corpus.is_file() and not reviewed_corpus.is_symlink()
                  and reviewed_corpus.stat().st_size <= MAX_ADDITIONS_BYTES,
                  "Explicit reviewed JSONL must be a regular file no larger than 4 MiB.")
        sources, _ = load_reviewed_corpus_window(reviewed_corpus, case_offset=case_offset,
                                                case_limit=case_limit, max_sources=64)
        selected = {choice["source_selection_request_sha256"] for source in sources for choice in source["choices"]}
        selected_cases = {}
        for case in iter_reviewed_requests(reviewed_corpus):
            key = case["source_selection_request_sha256"]
            if key in selected and key not in selected_cases:
                selected_cases[key] = case  # First duplicate metadata matches window loader.
        cases = list(selected_cases.values())
    _need(0 < len(cases) <= 256, "Capture requires a bounded selection of 1..256 reviewed cases.")
    pending = []
    for case in cases:
        selection = case["source_selection_request"]
        prepared = prepare_summary_request(selection)
        artifact = load_verified_cached_artifact(execution, prepared, selection)
        if artifact is not None:
            log = artifact["receipt"]["observation_log"]
            # load_verified_cached_artifact already enforced safe path, budget,
            # exact row/hash and closed schemas. Read again then put_result checks
            # bytes again, so a changed log cannot enter the stored result.
            with (execution / log["path"]).open("rb") as handle:
                raw = handle.read(1024 * 1024 + 1)
            _need(len(raw) <= 1024 * 1024, "Observation log exceeds its capture budget.")
            pending.append((artifact, raw))
    out.mkdir(parents=True, exist_ok=True)
    store = SummaryCorpus(out / "corpus")
    for case in cases:
        store.ingest_request(case["source_selection_request"], case["request_id"], review=case["review"])
    for artifact, raw in pending:
        store.put_result(artifact, observation_log_bytes=raw)
    store.archive(max_shards=256)
    completed = audit_handoff(out / "corpus")
    report = {
        "schema": "lab-summary-execution-capture/1", "status": "complete", "source_sha": source_sha,
        "review_source": "pinned_public_fixture" if reviewed_corpus is None else "explicit_reviewed_corpus",
        "selected_cases": len(cases), "captured_results": len(pending), "missing_results": len(cases) - len(pending),
        "case_coverage": summarize_case_coverage(iter(cases)), "new_generations": 0,
        "model_inference_performed": False, "counts_after": completed["counts"],
        "corpus_snapshot_sha256": completed["sha256"], "corpus_bytes": completed["size_bytes"], "corpus_path": "corpus",
        "publication": "Non-Pages handoff; reviewed-public data only. Capture neither runs a model nor uploads data.",
    }
    _write_json(out / "report.json", report)
    return report


def _synthetic_case(index: int) -> dict[str, Any]:
    request = {"schema": "lab-execution/1", "operation": "source_selection", "input": {
        "text": SYNTHETIC_TEXTS[index], "keywords": [], "sentence_ids": [],
    }}
    selected = execute(request)
    _need(selected.get("status") == "ok", "Could not analyze the fixed synthetic handoff fixture.")
    request["input"]["sentence_ids"] = [sentence["id"] for sentence in selected["sentences"]]
    return make_case(request, f"handoff-synthetic-round-{index + 1}", review=REVIEW)


def _round_subprocess(arguments: list[str]) -> None:
    command = [sys.executable, "-S", str(Path(__file__).resolve()), "round", *arguments]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        # Our child CLI emits content-free diagnostics with this explicit prefix.
        # Do not forward arbitrary tracebacks/stdout that could echo source data.
        stderr = getattr(exc, "stderr", "") or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        safe = [line for line in stderr.splitlines() if line.startswith("Incremental handoff failed: ")]
        detail = safe[-1][:2048] if safe else f"Child {type(exc).__name__}; no safe structured diagnostic was available."
        raise HandoffError("Clean-process handoff round failed or exceeded 300 seconds. " + detail) from exc


def run_check(*, out: Path, source_sha: str, model: Path, llama_server: Path) -> dict[str, Any]:
    """Exactly two real generations in two fresh processes, with one exact reuse."""
    _need(model is not None and llama_server is not None, "The check requires the already prepared pinned model/runtime; it never downloads them.")
    _need(re.fullmatch(r"[0-9a-f]{40}", source_sha or "") is not None, "source_sha must be an exact 40-character source commit.")
    out = _fresh_output(Path(out), [model, llama_server])
    out.mkdir(parents=True, exist_ok=True)
    inputs = out / "reviewed-inputs"
    inputs.mkdir()
    cases = [_synthetic_case(0), _synthetic_case(1)]
    for index, case in enumerate(cases, 1):
        (inputs / f"round-{index}.jsonl").write_text(canonical_request(case) + "\n", encoding="utf-8")
    common = ["--source-sha", source_sha, "--model", str(model.resolve()), "--llama-server", str(llama_server.resolve())]
    first = out / "round-1"
    second = out / "round-2"
    _round_subprocess(["--out", str(first.resolve()), "--append-reviewed-jsonl", str((inputs / "round-1.jsonl").resolve()), *common])
    _round_subprocess(["--out", str(second.resolve()), "--input-corpus", str((first / "corpus").resolve()),
                       "--append-reviewed-jsonl", str((inputs / "round-2.jsonl").resolve()), *common])
    reports = [json.loads((directory / "report.json").read_text(encoding="utf-8")) for directory in (first, second)]
    _need(reports[0]["worker_pid"] != reports[1]["worker_pid"] and all(report["worker_pid"] != os.getpid() for report in reports),
          "The incremental check did not use two separate child processes.")
    _need(reports[0]["new_generations"] == 1 and reports[0]["reused_results"] == 0
          and reports[1]["new_generations"] == 1 and reports[1]["reused_results"] == 1
          and all(report["generation_status"] == "generated" and report["model_inference_performed"] is True for report in reports),
          "Expected exactly one first-round generation, then one exact reuse and one new generation.")
    _need(reports[0]["counts_after"]["cases"] == 1 and reports[1]["counts_after"]["cases"] == 2
          and reports[1]["counts_after"]["results"] == 2
          and reports[1]["input_snapshot_sha256"] == reports[0]["corpus_snapshot_sha256"],
          "The second process did not restore the complete first-round corpus and append the different case.")
    manifests = [json.loads((directory / "execution/source-summary-manifest.json").read_text(encoding="utf-8")) for directory in (first, second)]
    old_key = cases[0]["source_selection_request_sha256"]
    old_entry, reused_entry = manifests[0]["entries"][old_key], manifests[1]["entries"][old_key]
    old_bytes = (first / "execution" / old_entry["receipt"]).read_bytes()
    reused_bytes = (second / "execution" / reused_entry["receipt"]).read_bytes()
    _need(reused_entry["reused"] is True and old_bytes == reused_bytes,
          "The previously generated artifact changed or was regenerated in the second process.")
    old_artifact = json.loads(old_bytes)
    log = old_artifact["receipt"]["observation_log"]
    old_log = (first / "execution" / log["path"]).read_bytes()
    _need(old_log == (second / "execution" / log["path"]).read_bytes(), "The previous execution log bytes were not preserved.")
    # The final handoff is independently reloadable without either round folder.
    final_snapshot = audit_handoff(second / "corpus")
    _copy_snapshot(second / "corpus", out / "corpus", final_snapshot)
    report = {
        "schema": "lab-incremental-summary-check/1", "status": "verified", "source_sha": source_sha,
        "check": "two_clean_processes_restore_archive_append_different_case_generate_only_missing",
        "fixture": "fixed_synthetic_reviewed_public_notebooks", "rounds": reports,
        "new_generations": 2, "reused_results": 1, "failed_generations": 0, "model_inference_performed": True,
        "case_coverage": summarize_case_coverage(SummaryCorpus(out / "corpus").iter_cases()),
        "previous_artifact_sha256": hashlib.sha256(old_bytes).hexdigest(),
        "previous_observation_log_sha256": hashlib.sha256(old_log).hexdigest(),
        "corpus_snapshot_sha256": final_snapshot["sha256"], "corpus_path": "corpus",
        "publication": "Non-Pages handoff artifact, not private storage; reviewed-public data only. No upload performed.",
    }
    _write_json(out / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("round", "check", "capture"):
        command = subparsers.add_parser(name)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--source-sha", required=True)
        if name != "capture":
            command.add_argument("--model", type=Path, required=name == "check")
            command.add_argument("--llama-server", type=Path, required=name == "check")
        else:
            command.add_argument("--execution", type=Path, required=True)
            command.add_argument("--reviewed-corpus", type=Path)
        if name == "round":
            command.add_argument("--input-corpus", type=Path)
            command.add_argument("--append-reviewed-jsonl", type=Path)
        if name in {"round", "capture"}:
            command.add_argument("--case-offset", type=int, default=0)
            command.add_argument("--case-limit", type=int, default=16)
    args = parser.parse_args()
    try:
        if args.command == "check":
            report = run_check(out=args.out, source_sha=args.source_sha, model=args.model, llama_server=args.llama_server)
        elif args.command == "capture":
            report = capture_execution(execution=args.execution, out=args.out, source_sha=args.source_sha,
                reviewed_corpus=args.reviewed_corpus, case_offset=args.case_offset, case_limit=args.case_limit)
        else:
            report = run_round(out=args.out, source_sha=args.source_sha, input_corpus=args.input_corpus,
                append_reviewed_jsonl=args.append_reviewed_jsonl, model=args.model, llama_server=args.llama_server,
                case_offset=args.case_offset, case_limit=args.case_limit)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Incremental handoff failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
