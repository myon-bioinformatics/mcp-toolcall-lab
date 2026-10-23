"""Synthetic offline contracts only. No mocked output is exported as generation."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
import zipfile

import pytest

from mcp_toolcall_lab import portable_summary_build as build
from mcp_toolcall_lab import portable_summary_corpus as corpus
from mcp_toolcall_lab.portable_execution import canonical_request, execute, request_sha256

REVIEW = {"classification": "reviewed_public", "reviewed_by": "offline test reviewer",
          "reviewed_at": "2026-10-23T00:00:00+00:00"}


def selection(text="Synthetic archive sample. Another unrelated sentence.", indexes=(0,)):
    selection_request = {"schema": "lab-execution/1", "operation": "source_selection",
               "input": {"text": text, "keywords": [], "sentence_ids": []}}
    initial = execute(selection_request)
    assert initial["status"] == "ok"
    selection_request["input"]["sentence_ids"] = [initial["sentences"][i]["id"] for i in indexes]
    return selection_request


@pytest.fixture(scope="module")
def selection_request():
    return selection()


def synthetic_result(selection_request):
    """A deliberately labeled unit-test object, never real model evidence."""
    prepared = build.prepare_summary_request(selection_request)
    source_key, key = request_sha256(selection_request), request_sha256(prepared)
    text = "SYNTHETIC UNIT TEST ONLY: no model was run."
    observed = {"schema": "lab-summary-observation/1", "event": "portable/summary_generation",
                "model_inference_performed": True, "source_selection_request_sha256": source_key,
                "summary_request_sha256": key, "wire_request_sha256": request_sha256(build.completion_body(prepared)),
                "generated_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                "elapsed_ms": 1, "finish_reason": "stop", "generated_at": "2026-10-23T00:00:00+00:00"}
    raw = (canonical_request(observed) + "\n").encode()
    log_key = hashlib.sha256(raw).hexdigest()
    receipt = {**observed, "schema": "lab-summary-receipt/1", "status": "generated",
               "execution_mode": "build_time_llama_cpp", "confidence": None,
               "generated_text": text, "truncated": False, "runtime_observed": {"version_output": "version: 0.4.1-dev (build 11046, commit 60081bb2b)\nSYNTHETIC UNIT TEST ONLY",
                   "binary_sha256": "f" * 64, "observed_default_top_k": 40,
                   "verification": "Owned local process; version/revision checked and executable bytes hashed. The version string alone is not producer authentication."},
               "confidence_reason": "Uncalibrated model output; no probability of correctness is claimed.",
               "determinism_note": build.DETERMINISM_NOTE,
               "verification": "Records one owned local llama.cpp execution. Hashes bind content, not truth or producer authenticity.",
               "observation_log": {"path": f"summary-logs/{log_key}.jsonl", "sha256": log_key,
                                   "line": 1, "line_sha256": hashlib.sha256(raw[:-1]).hexdigest()}}
    del receipt["event"]
    return {"schema": "lab-source-summary-artifact/1", "source_selection_request": selection_request,
            "source_selection_request_sha256": source_key, "summary_request": prepared,
            "summary_request_sha256": key, "receipt": receipt, "build": {"source_sha": "a" * 40, "build_run": None, "build_attempt": None}}, raw


def test_ingest_deduplicates_exact_requests_but_accumulates_changed_inputs(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    first = store.ingest_request(selection_request, "round-1")
    again = store.ingest_request(selection_request, "round-2")
    assert again == first  # ID is metadata, not permission to duplicate a case.
    different_selection = selection(indexes=(1,))
    different_input = selection("A genuinely different synthetic source. New data each round.")
    second = store.ingest_request(different_selection, "round-3")
    third = store.ingest_request(different_input, "round-4")
    assert len({case["source_selection_request_sha256"] for case in (first, second, third)}) == 3
    assert len(list(store.iter_cases())) == 3
    assert store.get_case(first["source_selection_request_sha256"]) == first
    assert store.get_case("f" * 64) is None
    assert store.check() == {"cases": 3, "results": 0, "logs": 0, "shards": 1}


def test_source_ids_offsets_hashes_and_private_default(tmp_path):
    selection_request = selection("😀資料。 Synthetic UTF-16 sentence.", indexes=(0, 1))
    store = corpus.SummaryCorpus(tmp_path)
    case = store.ingest_request(selection_request, "unicode")
    assert case["review"] is None
    assert case["offset_unit"] == "utf16_code_units"
    assert case["selected_source_evidence"][0]["end"] == 5
    for field, replacement in [("source_sha256", "f" * 64), ("source_bytes", 10), ("offset_unit", "bytes")]:
        changed = copy.deepcopy(case)
        changed[field] = replacement
        with pytest.raises(corpus.CorpusError):
            corpus.validate_case(changed)
    changed = copy.deepcopy(case)
    changed["selected_source_evidence"][1]["start"] -= 1
    with pytest.raises(corpus.CorpusError):
        corpus.validate_case(changed)
    changed = copy.deepcopy(selection_request)
    changed["input"]["text"] += " changed"
    with pytest.raises(corpus.CorpusError):
        store.ingest_request(changed, "stale")
    with pytest.raises(corpus.CorpusError, match="reviewed_public"):
        list(corpus.iter_reviewed_requests(tmp_path))


@pytest.mark.parametrize("request_id", ["", "../private", "space id", "x" * 129, "日本語", None])
def test_invalid_request_identifiers(tmp_path, selection_request, request_id):
    with pytest.raises(corpus.CorpusError, match="request_id"):
        corpus.SummaryCorpus(tmp_path).ingest_request(selection_request, request_id)


def test_schema_and_review_fail_closed(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    for review in [{}, {**REVIEW, "classification": "private"}, {**REVIEW, "reviewed_at": "2026-10-23"}, {**REVIEW, "extra": True}]:
        with pytest.raises(corpus.CorpusError):
            store.ingest_request(selection_request, "review", review=review)
    case = store.ingest_request(selection_request, "private")
    with pytest.raises(corpus.CorpusError, match="immutable review"):
        store.ingest_request(selection_request, "public", review=REVIEW)
    case["schema"] = "lab-summary-corpus-case/2"
    with pytest.raises(corpus.CorpusError, match="schema"):
        corpus.validate_case(case)


@pytest.mark.parametrize("raw", [b'\n', b'{"a":1}\nnot json\n', b'{"a":1}', b'{"a":1,"a":2}\n',
                                 b'{"a":NaN}\n', b'{"a":"\\ud800"}\n', b'{"a":9007199254740992}\n', b'[]\n'])
def test_strict_jsonl_never_silently_skips_corruption(tmp_path, raw):
    path = tmp_path / "input.jsonl"
    path.write_bytes(raw)
    with pytest.raises(corpus.CorpusError):
        list(corpus.iter_jsonl(path))


def test_json_structure_and_line_budgets(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text('{"a":' + '[' * 40 + '0' + ']' * 40 + '}\n')
    with pytest.raises(corpus.CorpusError, match="structure"):
        list(corpus.iter_jsonl(path))
    path.write_bytes(b'{"text":"' + b'a' * corpus.MAX_RECORD_BYTES + b'"}\n')
    with pytest.raises(corpus.CorpusError, match="Oversized"):
        list(corpus.iter_jsonl(path))


def test_reviewed_jsonl_projection_is_bounded_and_deduped(tmp_path, selection_request):
    case = corpus.make_case(selection_request, "public-round-1", review=REVIEW)
    path = tmp_path / "public.jsonl"
    path.write_text((canonical_request(case) + "\n") * 2)
    sources = corpus.load_reviewed_corpus(path)
    assert len(sources) == 1 and len(sources[0]["choices"]) == 1
    assert sources[0]["choices"][0]["label"] == "public-round-1"
    assert sources[0]["text"] == selection_request["input"]["text"]
    with pytest.raises(corpus.CorpusError, match="choice budget"):
        corpus.load_reviewed_corpus(path, max_choices=0)
    with pytest.raises(corpus.CorpusError, match="source budget"):
        corpus.load_reviewed_corpus(path, max_sources=0)


def test_exact_result_recipe_reuse_does_not_execute_model(tmp_path, selection_request):
    artifact, log = synthetic_result(selection_request)
    store = corpus.SummaryCorpus(tmp_path)
    with patch.object(build, "owned_server", side_effect=AssertionError("No model may run")):
        store.put_result(artifact, observation_log_bytes=log)
        store.put_result(artifact, observation_log_bytes=log)
        loaded = store.get_result(artifact["summary_request_sha256"], expected_request=artifact["summary_request"], expected_selection=selection_request)
        assert loaded == artifact
        assert store.get_observation_log(hashlib.sha256(log).hexdigest()) == log
        assert store.get_result("f" * 64) is None
        changed = copy.deepcopy(artifact["summary_request"])
        changed["system_prompt"] += " Changed recipe"
        assert store.get_result(request_sha256(changed)) is None
        with pytest.raises(corpus.CorpusError, match="exact expected"):
            store.get_result(artifact["summary_request_sha256"], expected_request=changed)
    assert store.check()["results"] == store.check()["logs"] == 1


def test_changed_sampler_is_not_reused_just_because_seed_is_fixed(tmp_path, selection_request):
    artifact, log = synthetic_result(selection_request)
    store = corpus.SummaryCorpus(tmp_path)
    store.put_result(artifact, log)
    for field in ("temperature", "top_k", "top_p", "repeat_penalty"):
        changed = copy.deepcopy(artifact["summary_request"])
        changed["sampler"][field] += 1
        assert changed["sampler"]["seed"] == artifact["summary_request"]["sampler"]["seed"]
        assert store.get_result(request_sha256(changed)) is None


@pytest.mark.parametrize("mutation", ["text", "usage", "offset", "schema", "recipe", "wire", "status", "log", "log_path", "line", "line_hash"])
def test_invalid_results_and_receipt_tampering_are_rejected(tmp_path, selection_request, mutation):
    artifact, raw = synthetic_result(selection_request)
    if mutation == "text": artifact["receipt"]["generated_text"] += " modified"
    elif mutation == "usage": artifact["receipt"]["usage"]["total_tokens"] += 1
    elif mutation == "offset": artifact["summary_request"]["selected_source_evidence"][0]["start"] += 1
    elif mutation == "schema": artifact["schema"] += "/unknown"
    elif mutation == "recipe": del artifact["summary_request"]["system_prompt"]
    elif mutation == "wire": artifact["receipt"]["wire_request_sha256"] = "f" * 64
    elif mutation == "status": artifact["receipt"]["model_inference_performed"] = False
    elif mutation == "log": raw += b"corrupt\n"
    elif mutation == "log_path": artifact["receipt"]["observation_log"]["path"] = "../../private"
    elif mutation == "line": artifact["receipt"]["observation_log"]["line"] = 2
    elif mutation == "line_hash": artifact["receipt"]["observation_log"]["line_sha256"] = "f" * 64
    with pytest.raises(corpus.CorpusError):
        corpus.SummaryCorpus(tmp_path).put_result(artifact, raw)
    assert list(tmp_path.iterdir()) == []


def test_receipt_requires_original_whole_log_and_matching_event(selection_request):
    artifact, raw = synthetic_result(selection_request)
    with pytest.raises(corpus.CorpusError):
        corpus.validate_summary_artifact(artifact)
    event = json.loads(raw)
    event["elapsed_ms"] += 1
    changed = (canonical_request(event) + "\n").encode()
    sha = hashlib.sha256(changed).hexdigest()
    artifact["receipt"]["observation_log"].update(sha256=sha, path=f"summary-logs/{sha}.jsonl", line_sha256=hashlib.sha256(changed[:-1]).hexdigest())
    with pytest.raises(corpus.CorpusError, match="do not match"):
        corpus.validate_summary_artifact(artifact, log_bytes=changed)


def test_prior_directory_cache_is_verified_before_reuse(tmp_path, selection_request):
    artifact, raw = synthetic_result(selection_request)
    key = artifact["summary_request_sha256"]
    assert corpus.load_verified_cached_artifact(tmp_path, artifact["summary_request"], selection_request) is None
    (tmp_path / "summaries").mkdir()
    (tmp_path / "summary-logs").mkdir()
    (tmp_path / "summaries" / f"{key}.json").write_text(json.dumps(artifact))
    log_path = tmp_path / artifact["receipt"]["observation_log"]["path"]
    log_path.write_bytes(raw)
    assert corpus.load_verified_cached_artifact(tmp_path, artifact["summary_request"], selection_request) == artifact
    log_path.write_bytes(raw + b"{}\n")
    with pytest.raises(corpus.CorpusError, match="checksum"):
        corpus.load_verified_cached_artifact(tmp_path, artifact["summary_request"], selection_request)


def test_archive_roundtrip_retains_immutable_refs_and_accepts_more_rounds(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    case = store.ingest_request(selection_request, "first")
    artifact, raw = synthetic_result(selection_request)
    store.put_result(artifact, raw)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in (tmp_path / "index").rglob("*.json")}
    archived = store.archive()
    assert len(archived) == 1
    assert list((tmp_path / "shards").iterdir()) == []
    assert store.get_case(case["source_selection_request_sha256"]) == case
    assert store.get_result(artifact["summary_request_sha256"]) == artifact
    for relative, contents in before.items():
        assert (tmp_path / relative).read_bytes() == contents
    store.ingest_request(selection("Fresh incremental data. The seed need not change."), "second")
    assert store.check()["cases"] == 2
    assert len(store.archive()) == 1
    assert store.archive() == []
    assert store.check()["shards"] == 2


def test_lazy_lookup_does_not_load_unrelated_case_or_archive(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    first = store.ingest_request(selection_request, "first")
    store.archive()
    second = store.ingest_request(selection("Different source. New observations."), "second")
    store.archive()
    second_ref = store._reference("cases", second["source_selection_request_sha256"])
    other_archive = tmp_path / "archives" / f"{second_ref['shard']}.zip"
    other_archive.write_bytes(b"deliberately broken unrelated archive")
    assert store.get_case(first["source_selection_request_sha256"]) == first
    with pytest.raises(corpus.CorpusError):
        store.check()  # Full audit, unlike keyed reads, checks all corpus records.


def test_corrupt_record_index_and_orphans_are_not_silently_skipped(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    case = store.ingest_request(selection_request, "first")
    key = case["source_selection_request_sha256"]
    ref = store._reference("cases", key)
    shard = tmp_path / "shards" / f"{ref['shard']}.jsonl"
    original = shard.read_bytes()
    shard.write_bytes(original.replace(b"first", b"worse"))
    with pytest.raises(corpus.CorpusError, match="checksum"):
        store.get_case(key)
    shard.write_bytes(original)
    ref_path = store._ref_path("cases", key)
    index_original = ref_path.read_bytes()
    ref["shard"] = "../../outside"
    ref_path.write_text(json.dumps(ref))
    with pytest.raises(corpus.CorpusError, match="Unsafe"):
        store.get_case(key)
    ref_path.write_bytes(index_original)
    with shard.open("ab") as handle:
        handle.write(original)
    with pytest.raises(corpus.CorpusError):
        store.check()
    with pytest.raises(corpus.CorpusError, match="Interrupted"):
        store.ingest_request(selection("A new request. Different data."), "second")


@pytest.mark.parametrize("kind", ["path", "symlink", "many", "bomb", "truncated"])
def test_archive_security_guards(tmp_path, selection_request, kind):
    store = corpus.SummaryCorpus(tmp_path)
    case = store.ingest_request(selection_request, "security")
    ref = store._reference("cases", case["source_selection_request_sha256"])
    shard = ref["shard"]
    store.archive()
    target = tmp_path / "archives" / f"{shard}.zip"
    if kind == "truncated":
        target.write_bytes(target.read_bytes()[:-3])
    else:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            if kind == "path": zipped.writestr("../outside.jsonl", b"{}\n")
            elif kind == "symlink":
                info = zipfile.ZipInfo(f"{shard}.jsonl")
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                zipped.writestr(info, b"/private\n")
            elif kind == "many":
                for index in range(2): zipped.writestr(f"{index}.jsonl", b"{}\n")
            else: zipped.writestr(f"{shard}.jsonl", b"x" * (corpus.MAX_SHARD_BYTES + 1))
    with pytest.raises(corpus.CorpusError):
        store.get_case(case["source_selection_request_sha256"])
    assert not (tmp_path.parent / "outside.jsonl").exists()


def test_writer_lock_and_symlinks_fail_without_overwriting(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path / "store")
    store.root.mkdir()
    lock = store.root / ".writer.lock"
    lock.write_text("interrupted writer")
    with pytest.raises(corpus.CorpusError, match="lock"):
        store.ingest_request(selection_request, "blocked")
    assert lock.read_text() == "interrupted writer"
    lock.unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (store.root / "index").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(corpus.CorpusError, match="Symlinks"):
        store.ingest_request(selection_request, "blocked")
    assert list(elsewhere.iterdir()) == []


def test_cli_local_ingest_export_archive_no_publish(tmp_path, selection_request):
    source = tmp_path / "request.json"
    source.write_text(json.dumps(selection_request))
    root = tmp_path / "corpus"
    import sys
    cmd = [sys.executable, "-m", "mcp_toolcall_lab.portable_summary_corpus", "--corpus", str(root)]
    result = json.loads(subprocess.check_output(cmd + ["ingest", str(source), "--request-id", "browser-export"], text=True))
    assert result["published"] is False and result["review"] is None
    exported = tmp_path / "cases.jsonl"
    subprocess.check_call(cmd + ["export-jsonl", str(exported)])
    assert len(list(corpus.iter_jsonl(exported))) == 1
    subprocess.check_call(cmd + ["archive"])
    assert json.loads(subprocess.check_output(cmd + ["check"], text=True))["cases"] == 1


def test_stable_disk_backed_windows_advance_and_report_snapshot(tmp_path):
    cases = [corpus.make_case(selection(f"Synthetic reviewed case {number}. New evidence."), f"round-{number}", review=REVIEW)
             for number in range(3)]
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(canonical_request(case) + "\n" for case in [*reversed(cases), cases[0]]))
    first, window = corpus.load_reviewed_corpus_window(path, case_limit=2)
    second, final = corpus.load_reviewed_corpus_window(path, case_offset=window["next_offset"], case_limit=2)
    keys = [choice["source_selection_request_sha256"] for source in first + second for choice in source["choices"]]
    assert keys == sorted(case["source_selection_request_sha256"] for case in cases)
    assert window["total_cases"] == final["total_cases"] == 3
    assert window["returned_cases"] == 2 and final["returned_cases"] == 1
    assert window["next_offset"] == 2 and window["has_more"] is True
    assert final["next_offset"] is None and final["has_more"] is False
    assert window["corpus_sha256"] == final["corpus_sha256"]
    path.write_text("".join(canonical_request(case) + "\n" for case in cases))
    reordered, stable = corpus.load_reviewed_corpus_window(path, case_limit=2)
    assert reordered == first and stable == window
    new = corpus.make_case(selection("A newly accumulated source. Additional evidence."), "round-4", review=REVIEW)
    with path.open("a") as handle: handle.write(canonical_request(new) + "\n")
    _, changed = corpus.load_reviewed_corpus_window(path, case_limit=2)
    assert changed["total_cases"] == 4 and changed["corpus_sha256"] != window["corpus_sha256"]
    for kwargs in [{"case_offset": 99}, {"case_offset": True}, {"case_limit": 0}, {"case_limit": 257}]:
        with pytest.raises(corpus.CorpusError): corpus.load_reviewed_corpus_window(path, **kwargs)


def test_window_validates_unselected_records_before_public_projection(tmp_path, selection_request):
    public = corpus.make_case(selection_request, "public", review=REVIEW)
    private = corpus.make_case(selection("Never publish this unreviewed source."), "private")
    path = tmp_path / "mixed.jsonl"
    path.write_text(canonical_request(public) + "\n" + canonical_request(private) + "\n")
    with pytest.raises(corpus.CorpusError, match="reviewed_public"):
        corpus.load_reviewed_corpus_window(path, case_limit=1)


@pytest.mark.parametrize("target", ["artifact", "receipt", "build", "runtime_observed", "model", "runtime", "sampler", "generation", "server"])
def test_cached_unknown_fields_cannot_smuggle_private_metadata(selection_request, target):
    artifact, log = synthetic_result(selection_request)
    if target == "artifact": pointer = artifact
    elif target in {"receipt", "build"}: pointer = artifact[target]
    elif target == "runtime_observed": pointer = artifact["receipt"][target]
    else: pointer = artifact["summary_request"][target]
    pointer["unreviewed_private_metadata"] = "PRIVATE TEST DATA MUST NEVER BE EXPORTED"
    # Repair the content key too: closed schemas, not only hash mismatches, must reject this.
    key = request_sha256(artifact["summary_request"])
    artifact["summary_request_sha256"] = key
    artifact["receipt"]["summary_request_sha256"] = key
    with pytest.raises(corpus.CorpusError, match="[Uu]nknown|[Ii]ncomplete"):
        corpus.validate_summary_artifact(artifact, log_bytes=log)


@pytest.mark.parametrize("where", ["selected", "other", "other_usage", "other_invalid"])
def test_all_copied_log_lines_are_closed_schema_even_after_rehash(selection_request, where):
    artifact, original = synthetic_result(selection_request)
    selected = json.loads(original)
    other = copy.deepcopy(selected)
    if where == "selected": selected["unreviewed_private_text"] = "PRIVATE TEST DATA"
    elif where == "other": other["unreviewed_private_text"] = "PRIVATE TEST DATA"
    elif where == "other_usage": other["usage"]["unreviewed_private_text"] = "PRIVATE TEST DATA"
    else: other = {"private_source": "PRIVATE TEST DATA"}
    selected_raw = (canonical_request(selected) + "\n").encode()
    raw = selected_raw + (canonical_request(other) + "\n").encode()
    sha = hashlib.sha256(raw).hexdigest()
    artifact["receipt"]["observation_log"].update(sha256=sha, path=f"summary-logs/{sha}.jsonl",
        line_sha256=hashlib.sha256(selected_raw[:-1]).hexdigest())
    with pytest.raises(corpus.CorpusError, match="observation|[Oo]bservation"):
        corpus.validate_summary_artifact(artifact, log_bytes=raw)


def test_case_evidence_boolean_cannot_impersonate_integer_offset(selection_request):
    case = corpus.make_case(selection_request, "strict-types")
    assert case["selected_source_evidence"][0]["start"] == 0
    case["selected_source_evidence"][0]["start"] = False
    with pytest.raises(corpus.CorpusError): corpus.validate_case(case)


def test_shard_rollover_stays_bounded_and_lookup_is_still_lazy(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    with patch.object(corpus, "MAX_SHARD_RECORDS", 1):
        first = store.ingest_request(selection_request, "one")
        second = store.ingest_request(selection("Second distinct input. More information."), "two")
        assert len(list((tmp_path / "shards").iterdir())) == 2
        assert len(store.archive(max_shards=1)) == 1
        assert store.get_case(first["source_selection_request_sha256"]) == first
        assert store.get_case(second["source_selection_request_sha256"]) == second
        assert store.check()["shards"] == 2


def test_active_state_corruption_and_duplicate_archive_fail_audit(tmp_path, selection_request):
    store = corpus.SummaryCorpus(tmp_path)
    case = store.ingest_request(selection_request, "active")
    active_path = tmp_path / "active.json"
    active = json.loads(active_path.read_text())
    active["records"] += 1
    active_path.write_text(json.dumps(active))
    with pytest.raises(corpus.CorpusError, match="record count"):
        store.check()
    active["records"] -= 1
    active_path.write_text(json.dumps(active))
    ref = store._reference("cases", case["source_selection_request_sha256"])
    original = (tmp_path / "shards" / f"{ref['shard']}.jsonl").read_bytes()
    store.archive()
    (tmp_path / "shards" / f"{ref['shard']}.jsonl").write_bytes(original)
    with pytest.raises(corpus.CorpusError, match="Both"):
        store.check()


def test_observation_log_line_limit_matches_browser_node(selection_request):
    artifact, original = synthetic_result(selection_request)
    too_many = original * 1001
    assert len(too_many) < corpus.MAX_LOG_BYTES
    sha = hashlib.sha256(too_many).hexdigest()
    artifact["receipt"]["observation_log"].update(sha256=sha, path=f"summary-logs/{sha}.jsonl")
    with pytest.raises(corpus.CorpusError, match="Too many"):
        corpus.validate_summary_artifact(artifact, log_bytes=too_many)
    artifact["receipt"]["observation_log"]["line"] = 1001
    with pytest.raises(corpus.CorpusError, match="line"):
        corpus.validate_summary_artifact(artifact, log_bytes=too_many)


def test_observation_crlf_is_rejected_consistently_with_browser(selection_request):
    artifact, original = synthetic_result(selection_request)
    raw = original.replace(b'\n', b'\r\n')
    sha = hashlib.sha256(raw).hexdigest()
    artifact['receipt']['observation_log'].update(sha256=sha, path=f'summary-logs/{sha}.jsonl',
        line_sha256=hashlib.sha256(raw.rstrip(b'\n')).hexdigest())
    with pytest.raises(corpus.CorpusError, match='LF newlines'):
        corpus.validate_summary_artifact(artifact, log_bytes=raw)


def multi_selection(text="Alpha lantern glows. Beta garden grows. Alpha garden rests."):
    selected = selection(text, indexes=(0, 1, 2))
    analyzed = execute(selected)
    wanted = {"Alpha", "garden"}
    selected["input"]["keywords"] = [option["id"] for option in analyzed["keyword_options"] if option["text"] in wanted]
    assert len(selected["input"]["keywords"]) == 2
    return selected


def test_reordered_keyword_and_sentence_ids_are_one_canonical_case(tmp_path):
    original = multi_selection()
    reversed_order = copy.deepcopy(original)
    reversed_order["input"]["keywords"].reverse()
    reversed_order["input"]["sentence_ids"].reverse()
    unchanged = copy.deepcopy(reversed_order)
    assert request_sha256(reversed_order) != request_sha256(original)
    assert corpus.normalize_selection_request(reversed_order) == original
    assert reversed_order == unchanged  # Never mutate the caller's export.
    store = corpus.SummaryCorpus(tmp_path)
    first = store.ingest_request(reversed_order, "first-caller-id")
    same = store.ingest_request(original, "different-caller-id")
    assert first == same
    assert first["source_selection_request"] == original
    assert first["source_selection_request_sha256"] == request_sha256(original)
    assert first["request_id"] == "first-caller-id"
    assert store.check()["cases"] == 1
    assert len(list(store.iter_cases())) == 1


def test_normalization_preserves_source_bytes_and_distinguishes_meaningful_subsets(tmp_path):
    text = "😀 Alpha lantern glows.\r\n  Beta garden grows.\nAlpha garden rests. e\u0301"
    original = multi_selection(text)
    normalized = corpus.normalize_selection_request(original)
    assert normalized["input"]["text"].encode("utf-8") == text.encode("utf-8")
    store = corpus.SummaryCorpus(tmp_path)
    cases = [store.ingest_request(original, "full")]
    smaller_evidence = copy.deepcopy(original)
    smaller_evidence["input"]["sentence_ids"] = smaller_evidence["input"]["sentence_ids"][:1]
    cases.append(store.ingest_request(smaller_evidence, "one-sentence"))
    different_keywords = copy.deepcopy(smaller_evidence)
    different_keywords["input"]["keywords"] = different_keywords["input"]["keywords"][:1]
    cases.append(store.ingest_request(different_keywords, "one-keyword"))
    # Each fresh source is analyzed again so its IDs are valid, not silently repaired.
    cases.append(store.ingest_request(multi_selection(text.replace("lantern", "beacon")), "source-edit"))
    cases.append(store.ingest_request(multi_selection(text.replace("e\u0301", "é")), "unicode-edit"))
    cases.append(store.ingest_request(multi_selection(text + " "), "whitespace-edit"))
    assert len({case["source_selection_request_sha256"] for case in cases}) == 6
    assert store.check()["cases"] == 6


def test_normalization_does_not_rescue_stale_duplicate_or_invalid_unicode_ids():
    original = multi_selection()
    invalid = []
    stale = copy.deepcopy(original)
    stale["input"]["text"] += " Changed."
    invalid.append(stale)
    duplicate = copy.deepcopy(original)
    duplicate["input"]["keywords"].append(duplicate["input"]["keywords"][0])
    invalid.append(duplicate)
    bad_unicode = copy.deepcopy(original)
    bad_unicode["input"]["text"] += "\ud800"
    invalid.append(bad_unicode)
    for value in invalid:
        with pytest.raises(corpus.CorpusError): corpus.normalize_selection_request(value)


def test_case_normalization_does_not_rewrite_exact_generated_recipe_identity():
    selected = multi_selection()
    reversed_order = copy.deepcopy(selected)
    reversed_order["input"]["keywords"].reverse()
    reversed_order["input"]["sentence_ids"].reverse()
    canonical_case = corpus.make_case(reversed_order, "canonical-case")
    assert canonical_case["source_selection_request"] == selected
    canonical_summary = build.prepare_summary_request(selected)
    original_summary = build.prepare_summary_request(reversed_order)
    assert request_sha256(canonical_summary) != request_sha256(original_summary)
    artifact, log = synthetic_result(reversed_order)
    verified = corpus.validate_summary_artifact(artifact, log_bytes=log, expected_summary_request=original_summary)
    assert verified["source_selection_request"] == reversed_order
    assert verified["summary_request_sha256"] == request_sha256(original_summary)


def test_coverage_counts_unique_cases_sources_and_objective_buckets():
    original = multi_selection()
    reversed_order = copy.deepcopy(original)
    reversed_order["input"]["keywords"].reverse()
    reversed_order["input"]["sentence_ids"].reverse()
    subset = copy.deepcopy(original)
    subset["input"]["keywords"] = []
    subset["input"]["sentence_ids"] = subset["input"]["sentence_ids"][:1]
    other = selection("A genuinely different source. New content.")
    cases = [corpus.make_case(value, f"caller-{index}") for index, value in enumerate((original, reversed_order, subset, other))]
    coverage = corpus.summarize_case_coverage(iter(cases))
    assert coverage["discovered_cases"] == 4
    assert coverage["unique_cases"] == 3
    assert coverage["duplicate_cases"] == 1
    assert coverage["distinct_sources"] == 2
    assert coverage["keyword_count_buckets"] == {"0": 2, "1": 0, "2-4": 1, "5-8": 0, "9+": 0}
    assert coverage["selected_sentence_count_buckets"] == {"1": 2, "2-4": 1, "5-8": 0, "9+": 0}
    assert "generated" not in json.dumps(coverage) and "accuracy" not in json.dumps(coverage)
    empty = corpus.summarize_case_coverage(iter(()))
    assert empty["unique_cases"] == empty["distinct_sources"] == 0
    assert all(value == 0 for value in empty["keyword_count_buckets"].values())


def test_coverage_rejects_invalid_records_instead_of_inventing_counts(selection_request):
    case = corpus.make_case(selection_request, "invalid-coverage")
    case["source_sha256"] = "f" * 64
    with pytest.raises(corpus.CorpusError):
        corpus.summarize_case_coverage(iter([case]))


@pytest.mark.parametrize("version", [
    "version: 0.4.1-dev (build 11046, commit 60081bb2b)",
    "version: 11046 (60081bb2)",
    "version: b11046 (60081bb)",
    f"version: 0.4.1-dev (build 11046, commit {build.RUNTIME['revision']})",
])
def test_runtime_observation_accepts_recorded_build_and_entire_revision(selection_request, version):
    artifact, raw = synthetic_result(selection_request)
    artifact['receipt']['runtime_observed']['version_output'] = version
    assert corpus.validate_summary_artifact(artifact, log_bytes=raw) == artifact


@pytest.mark.parametrize("version", [
    "version: 0.4.1-dev (build 11045, commit 60081bb2b)",
    "version: 0.4.1-dev (build 11046, commit deadbeef0)",
    "version: 0.4.1-dev (build 11046, commit 60081bb2b0)",
    "version: 0.4.1-dev (build 11046, commit 60081bbdeadbeef)",
    "version: 11046 (60081bbdeadbeef)",
    "version: 11046 (60081bb2b0)",
    f"version: 0.4.1-dev (build 11046, commit {build.RUNTIME['revision']}0)",
    "version: 11046 (60081bb2)\nversion: 11045 (60081bb2)",
])
def test_runtime_observation_rejects_wrong_build_or_commit_extension(selection_request, version):
    artifact, raw = synthetic_result(selection_request)
    artifact['receipt']['runtime_observed']['version_output'] = version
    with pytest.raises(corpus.CorpusError, match='version/revision'):
        corpus.validate_summary_artifact(artifact, log_bytes=raw)


def test_artifact_validator_uses_shared_runtime_matcher(selection_request):
    artifact, raw = synthetic_result(selection_request)
    with patch.object(build, 'runtime_version_matches', return_value=False) as matcher:
        with pytest.raises(corpus.CorpusError, match='version/revision'):
            corpus.validate_summary_artifact(artifact, log_bytes=raw)
    matcher.assert_called_once_with(artifact['receipt']['runtime_observed']['version_output'], artifact['summary_request']['runtime'])
