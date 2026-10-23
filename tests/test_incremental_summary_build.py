"""Handoff contracts in real clean Python processes, with explicit offline stubs.

No GGUF, llama.cpp, network or Docker runs here. Stub artifacts stay under pytest
TemporaryDirectory paths; they are never repository/public fixtures.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from mcp_toolcall_lab.portable_execution import canonical_request, execute
from mcp_toolcall_lab.portable_summary_corpus import SummaryCorpus, make_case

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/check_incremental_summary_build.py'
spec = importlib.util.spec_from_file_location('incremental_check', SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
SOURCE_SHA = 'a' * 40


def write_case(path, *, text=None, reviewed=True):
    if text is None:
        case = handoff._synthetic_case(0)
        if not reviewed:
            case['review'] = None
    else:
        request = {'schema': 'lab-execution/1', 'operation': 'source_selection', 'input': {
            'text': text, 'keywords': [], 'sentence_ids': [],
        }}
        request['input']['sentence_ids'] = [item['id'] for item in execute(request)['sentences']]
        case = make_case(request, 'explicit-addition', review=handoff.REVIEW if reviewed else None)
    path.write_text(canonical_request(case) + '\n', encoding='utf-8')
    return case


@pytest.fixture
def offline_child(tmp_path, monkeypatch):
    """Test-only wrapper patches generation inside each NEW Python process."""
    wrapper = tmp_path / 'offline_child.py'
    wrapper.write_text('''import hashlib, json, os, runpy, sys
from contextlib import contextmanager
from unittest.mock import patch
from mcp_toolcall_lab import portable_summary_build as generation
class OfflineClient:
    def request(self,path,body):
        assert path == '/v1/chat/completions'
        with open(os.environ['OFFLINE_CALL_LEDGER'],'a') as ledger:
            ledger.write(json.dumps({'pid':os.getpid(),'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()})+'\\n')
        return {'choices':[{'message':{'role':'assistant','content':'SYNTHETIC OFFLINE UNIT TEST; no model was executed.'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':50,'completion_tokens':10,'total_tokens':60}}
@contextmanager
def fake_server(*args):
    yield OfflineClient(), {'version_output':'version: 11046 (60081bb2)\\nSYNTHETIC UNIT TEST ONLY',
      'binary_sha256':'f'*64,'verification':'Owned local process; version/revision checked and executable bytes hashed. The version string alone is not producer authentication.',
      'observed_default_top_k':40}
with patch.object(generation,'owned_server',fake_server):
    sys.argv[0]=os.environ['HANDOFF_HELPER_SCRIPT']
    runpy.run_path(sys.argv[0],run_name='__main__')
''')
    model, runtime = tmp_path / 'test-only-model', tmp_path / 'test-only-runtime'
    model.write_text('Unit test placeholder. Never loaded.')
    runtime.write_text('Unit test placeholder. Never executed.')
    ledger = tmp_path / 'offline-calls.jsonl'
    environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'OFFLINE_CALL_LEDGER': str(ledger), 'HANDOFF_HELPER_SCRIPT': str(SCRIPT)}
    commands = []
    def run(arguments):
        command = [sys.executable, '-S', str(wrapper), 'round', *arguments]
        commands.append(command)
        subprocess.run(command, env=environment, capture_output=True, text=True, check=True, timeout=30)
    monkeypatch.setattr(handoff, '_round_subprocess', run)
    return model, runtime, ledger, commands, run


def test_two_clean_processes_preserve_complete_archive_and_generate_only_missing(tmp_path, offline_child):
    model, runtime, ledger, commands, _ = offline_child
    output = tmp_path / 'handoff'
    report = handoff.run_check(out=output, source_sha=SOURCE_SHA, model=model, llama_server=runtime)
    assert len(commands) == 2
    calls = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(calls) == 2 and calls[0]['pid'] != calls[1]['pid']
    assert calls[0]['request_sha256'] != calls[1]['request_sha256']
    assert report['status'] == 'verified'
    assert [(r['new_generations'], r['reused_results']) for r in report['rounds']] == [(1, 0), (1, 1)]
    assert report['rounds'][1]['input_snapshot_sha256'] == report['rounds'][0]['corpus_snapshot_sha256']
    store = SummaryCorpus(output / 'corpus')
    assert store.check()['cases'] == 2 and store.check()['results'] == 2
    assert all(case['review']['classification'] == 'reviewed_public' for case in store.iter_cases())
    assert not list((output / 'corpus/shards').glob('*.jsonl'))
    assert list((output / 'corpus/archives').glob('*.zip'))
    assert not list(output.rglob('*.gguf'))
    assert not list(output.rglob('*.so'))
    assert handoff.audit_handoff(output / 'corpus')['sha256'] == report['corpus_snapshot_sha256']
    for artifact in (output / 'round-1/execution/summaries').glob('*.json'):
        later = output / 'round-2/execution/summaries' / artifact.name
        assert artifact.read_bytes() == later.read_bytes()
        log = json.loads(artifact.read_text())['receipt']['observation_log']['path']
        assert (output / 'round-1/execution' / log).read_bytes() == (output / 'round-2/execution' / log).read_bytes()
    # The final handoff is usable without either original round directory.
    assert set(item['source_selection_request']['input']['text'] for item in store.iter_cases()) == set(handoff.SYNTHETIC_TEXTS)


def test_explicit_next_clean_round_imports_handoff_appends_different_case(tmp_path, offline_child):
    model, runtime, ledger, _, run = offline_child
    prior = tmp_path / 'prior'
    handoff.run_check(out=prior, source_sha=SOURCE_SHA, model=model, llama_server=runtime)
    input_hash = handoff.audit_handoff(prior / 'corpus')['sha256']
    addition = tmp_path / 'new-reviewed.jsonl'
    case = write_case(addition, text='A third fictional archive stores a red notebook. The catalogue now tracks daily observations.')
    following = tmp_path / 'following'
    run(['--out', str(following), '--source-sha', 'b' * 40, '--input-corpus', str(prior / 'corpus'),
         '--append-reviewed-jsonl', str(addition), '--model', str(model), '--llama-server', str(runtime)])
    report = json.loads((following / 'report.json').read_text())
    assert report['new_generations'] == 1 and report['reused_results'] == 2
    assert report['distinct_cases_added'] == 1
    assert report['counts_after']['cases'] == report['counts_after']['results'] == 3
    assert handoff.audit_handoff(prior / 'corpus')['sha256'] == input_hash  # input never mutated
    assert SummaryCorpus(following / 'corpus').get_case(case['source_selection_request_sha256']) == case
    assert len(ledger.read_text().splitlines()) == 3
    # Explicit duplicate additions do not grow the corpus or generate again.
    repeated = tmp_path / 'repeated'
    with patch.object(handoff, 'MAX_CASES', 3):
        again = handoff.run_round(out=repeated, source_sha='c' * 40, input_corpus=following / 'corpus', append_reviewed_jsonl=addition)
    assert again['distinct_cases_added'] == 0
    assert again['new_generations'] == 0 and again['reused_results'] == 3
    assert again['model_inference_performed'] is False


def test_no_runtime_round_is_not_presented_as_real_generation(tmp_path):
    addition = tmp_path / 'seed.jsonl'
    write_case(addition)
    report = handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, append_reviewed_jsonl=addition)
    assert report['new_generations'] == report['reused_results'] == 0
    assert report['model_inference_performed'] is False
    assert report['generation_status'] == 'not_generated'
    assert report['counts_after']['results'] == 0
    assert not (tmp_path / 'out/execution/summary-build.jsonl').exists()
    with pytest.raises(handoff.HandoffError, match='requires'):
        handoff.run_check(out=tmp_path / 'bad-check', source_sha=SOURCE_SHA, model=None, llama_server=None)


@pytest.mark.parametrize('extra', ['personal.txt', '.env', '.writer.lock', 'index/cases/not-a-prefix'])
def test_unexpected_files_never_transfer(tmp_path, extra):
    input_root = tmp_path / 'input'
    case = handoff._synthetic_case(0)
    SummaryCorpus(input_root).ingest_request(case['source_selection_request'], case['request_id'], review=case['review'])
    extra_path = input_root / extra
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_text('Private test marker: must never be copied.')
    output = tmp_path / 'out'
    with pytest.raises(handoff.HandoffError):
        handoff.run_round(out=output, source_sha=SOURCE_SHA, input_corpus=input_root)
    assert not output.exists()
    assert extra_path.read_text() == 'Private test marker: must never be copied.'


def test_unreviewed_addition_and_unreviewed_existing_case_fail_before_write(tmp_path):
    addition = tmp_path / 'private.jsonl'
    case = write_case(addition, reviewed=False)
    with pytest.raises(handoff.HandoffError, match='reviewed_public'):
        handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, append_reviewed_jsonl=addition)
    assert not (tmp_path / 'out').exists()
    source = tmp_path / 'private-corpus'
    SummaryCorpus(source).ingest_request(case['source_selection_request'], case['request_id'])
    with pytest.raises(handoff.HandoffError, match='Unreviewed'):
        handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, input_corpus=source)
    assert not (tmp_path / 'out').exists()


def test_symlink_overlap_and_size_limits_fail_before_write(tmp_path):
    addition = tmp_path / 'seed.jsonl'
    case = write_case(addition)
    source = tmp_path / 'input'
    SummaryCorpus(source).ingest_request(case['source_selection_request'], case['request_id'], review=case['review'])
    alias = tmp_path / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(handoff.HandoffError, match='regular directory'):
        handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, input_corpus=alias)
    with pytest.raises(handoff.HandoffError, match='overlap'):
        handoff.run_round(out=source / 'nested', source_sha=SOURCE_SHA, input_corpus=source)
    with patch.object(handoff, 'MAX_HANDOFF_BYTES', 1), pytest.raises(handoff.HandoffError, match='64 MiB'):
        handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, input_corpus=source)
    with patch.object(handoff, 'MAX_FILES', 1), pytest.raises(handoff.HandoffError, match='4096 files'):
        handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, input_corpus=source)
    assert not (tmp_path / 'out').exists()
    # Internal symlink files are rejected even when their target has safe bytes.
    active = source / 'active.json'
    copied = tmp_path / 'active-copy.json'
    copied.write_bytes(active.read_bytes())
    active.unlink()
    active.symlink_to(copied)
    with pytest.raises(handoff.HandoffError, match='symlink'):
        handoff.audit_handoff(source)


def test_child_failure_preserves_only_content_free_structured_diagnostic():
    failure = subprocess.CalledProcessError(2, ['child'], stderr='PRIVATE SOURCE TEXT\nIncremental handoff failed: Model SHA-256 does not match the pinned public GGUF.\n')
    with patch.object(handoff.subprocess, 'run', side_effect=failure), pytest.raises(handoff.HandoffError) as observed:
        handoff._round_subprocess([])
    assert 'Model SHA-256' in str(observed.value)
    assert 'PRIVATE SOURCE TEXT' not in str(observed.value)


def test_existing_output_and_invalid_input_do_not_mutate(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_text('keep')
    seed = tmp_path / 'seed.jsonl'
    write_case(seed)
    with pytest.raises(handoff.HandoffError, match='fresh empty'):
        handoff.run_round(out=output, source_sha=SOURCE_SHA, append_reviewed_jsonl=seed)
    assert marker.read_text() == 'keep'
    with pytest.raises(handoff.HandoffError, match='input corpus'):
        handoff.run_round(out=tmp_path / 'new', source_sha=SOURCE_SHA)
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('metadata', ['archive_comment', 'member_comment', 'member_extra', 'preamble', 'trailing'])
def test_unreviewed_zip_metadata_is_rejected_before_any_copy(tmp_path, metadata):
    import struct
    import zipfile
    source = tmp_path / 'input'
    case = handoff._synthetic_case(0)
    store = SummaryCorpus(source)
    store.ingest_request(case['source_selection_request'], case['request_id'], review=case['review'])
    store.archive()
    archive = next((source / 'archives').glob('*.zip'))
    marker = b'UNREVIEWED PRIVATE METADATA DO NOT PUBLISH'
    if metadata == 'archive_comment':
        with zipfile.ZipFile(archive, 'a') as zipped:
            zipped.comment = marker
    elif metadata in {'member_comment', 'member_extra'}:
        with zipfile.ZipFile(archive) as zipped:
            info = zipped.infolist()[0]
            raw = zipped.read(info.filename)
        if metadata == 'member_comment':
            info.comment = marker
        else:
            info.extra = struct.pack('<HH', 0x1234, len(marker)) + marker
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr(info, raw)
    elif metadata == 'preamble':
        archive.write_bytes(marker + archive.read_bytes())
    else:
        archive.write_bytes(archive.read_bytes() + marker)
    before = archive.read_bytes()
    with pytest.raises((handoff.HandoffError, ValueError)):
        handoff.audit_handoff(source)
    with pytest.raises((handoff.HandoffError, ValueError)):
        handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, input_corpus=source)
    assert not (tmp_path / 'out').exists()
    assert archive.read_bytes() == before


def test_capture_actual_pinned_pages_results_then_reload_without_inference(tmp_path, offline_child):
    from mcp_toolcall_lab.portable_summary_build import declared_sources
    model, runtime, ledger, _, run = offline_child
    reviewed = tmp_path / 'reviewed.jsonl'
    cases = [make_case(choice['source_selection_request'], choice['id'], review=handoff.REVIEW)
             for source in declared_sources() for choice in source['choices']]
    reviewed.write_text(''.join(canonical_request(case) + '\n' for case in cases))
    original = tmp_path / 'original'
    run(['--out', str(original), '--source-sha', SOURCE_SHA, '--append-reviewed-jsonl', str(reviewed),
         '--model', str(model), '--llama-server', str(runtime)])
    execution = original / 'execution'
    (execution / 'DO-NOT-TRANSFER.txt').write_text('Private unrelated execution file.')
    # Capture independently selects from the pinned fixture; it does not trust
    # arbitrary manifest text or copy its fields into the handoff.
    (execution / 'source-summary-manifest.json').write_text('{"review": "private unrelated manifest"}')
    captured = tmp_path / 'captured'
    with patch.object(handoff, 'build_summaries', side_effect=AssertionError('capture must not invoke a build')):
        report = handoff.capture_execution(execution=execution, out=captured, source_sha=SOURCE_SHA)
    assert report['captured_results'] == report['selected_cases'] == 4
    assert report['missing_results'] == report['new_generations'] == 0
    assert report['model_inference_performed'] is False
    store = SummaryCorpus(captured / 'corpus')
    assert {item['source_selection_request_sha256'] for item in store.iter_cases()} == {case['source_selection_request_sha256'] for case in cases}
    assert all(case['review']['classification'] == 'reviewed_public' for case in store.iter_cases())
    assert not list(captured.rglob('DO-NOT-TRANSFER.txt'))
    assert set(path.name for path in captured.iterdir()) == {'corpus', 'report.json'}
    restored = tmp_path / 'restored'
    result = handoff.run_round(out=restored, source_sha='b' * 40, input_corpus=captured / 'corpus')
    assert result['new_generations'] == 0 and result['reused_results'] == 4
    assert result['model_inference_performed'] is False
    assert len(ledger.read_text().splitlines()) == 4
    for artifact in execution.glob('summaries/*.json'):
        assert artifact.read_bytes() == (restored / 'execution/summaries' / artifact.name).read_bytes()
        path = json.loads(artifact.read_text())['receipt']['observation_log']['path']
        assert (execution / path).read_bytes() == (restored / 'execution' / path).read_bytes()


def test_capture_preserves_explicit_review_and_rejects_corruption(tmp_path, offline_child):
    model, runtime, _, _, run = offline_child
    reviewed = tmp_path / 'reviewed.jsonl'
    case = write_case(reviewed)
    case['review'] = {**case['review'], 'reviewed_by': 'explicit-original-reviewer'}
    reviewed.write_text(canonical_request(case) + '\n')
    original = tmp_path / 'original'
    run(['--out', str(original), '--source-sha', SOURCE_SHA, '--append-reviewed-jsonl', str(reviewed),
         '--model', str(model), '--llama-server', str(runtime)])
    captured = tmp_path / 'captured'
    report = handoff.capture_execution(execution=original / 'execution', out=captured, source_sha=SOURCE_SHA,
                                       reviewed_corpus=reviewed)
    assert report['captured_results'] == 1
    assert next(SummaryCorpus(captured / 'corpus').iter_cases())['review'] == case['review']
    artifact = next((original / 'execution/summaries').glob('*.json'))
    value = json.loads(artifact.read_text())
    value['receipt']['generated_text'] = 'Corrupted test text'
    artifact.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        handoff.capture_execution(execution=original / 'execution', out=tmp_path / 'bad', source_sha=SOURCE_SHA,
                                  reviewed_corpus=reviewed)
    assert not (tmp_path / 'bad').exists()
    case['review'] = None
    reviewed.write_text(canonical_request(case) + '\n')
    with pytest.raises(ValueError, match='reviewed_public'):
        handoff.capture_execution(execution=original / 'execution', out=tmp_path / 'private', source_sha=SOURCE_SHA,
                                  reviewed_corpus=reviewed)
    assert not (tmp_path / 'private').exists()


def test_capture_missing_results_is_honest_and_no_arbitrary_files_transfer(tmp_path):
    execution = tmp_path / 'execution'
    execution.mkdir()
    (execution / 'arbitrary-personal.txt').write_text('Must not transfer.')
    captured = tmp_path / 'captured'
    report = handoff.capture_execution(execution=execution, out=captured, source_sha=SOURCE_SHA)
    assert report['captured_results'] == 0 and report['missing_results'] == 4
    assert report['counts_after']['cases'] == 4 and report['counts_after']['results'] == 0
    assert not list(captured.rglob('arbitrary-personal.txt'))


def test_zip_preflight_bounds_before_constructing_zipfile(tmp_path):
    import struct
    source = tmp_path / 'input'
    case = handoff._synthetic_case(0)
    store = SummaryCorpus(source)
    store.ingest_request(case['source_selection_request'], case['request_id'], review=case['review'])
    store.archive()
    archive = next((source / 'archives').glob('*.zip'))
    raw = archive.read_bytes()
    end = bytearray(raw[-22:])
    offset = struct.unpack_from('<L', end, 16)[0]
    struct.pack_into('<L', end, 12, 84 + 1024)
    archive.write_bytes(raw[:-22] + b'X' * 1024 + end)
    with patch.object(handoff.zipfile, 'ZipFile', side_effect=AssertionError('preflight must reject first')):
        with pytest.raises(handoff.HandoffError, match='layout'):
            handoff._audit_archive_metadata(archive)
    archive.write_bytes(raw)
    with patch.object(handoff, 'MAX_ARCHIVE_BYTES', len(raw) - 1):
        with pytest.raises(handoff.HandoffError, match='budget'):
            handoff._audit_archive_metadata(archive)


def test_zip_unused_compressed_payload_cannot_hide_private_bytes(tmp_path):
    import struct
    import zipfile
    source = tmp_path / 'input'
    case = handoff._synthetic_case(0)
    store = SummaryCorpus(source)
    store.ingest_request(case['source_selection_request'], case['request_id'], review=case['review'])
    store.archive()
    archive = next((source / 'archives').glob('*.zip'))
    raw = bytearray(archive.read_bytes())
    offset = struct.unpack_from('<L', raw, len(raw) - 6)[0]
    marker = b'PRIVATE UNUSED COMPRESSED PAYLOAD'
    compressed = struct.unpack_from('<L', raw, 18)[0]
    struct.pack_into('<L', raw, 18, compressed + len(marker))
    struct.pack_into('<L', raw, offset + 20, compressed + len(marker))
    struct.pack_into('<L', raw, len(raw) - 6, offset + len(marker))
    raw[offset:offset] = marker
    archive.write_bytes(raw)
    # Ordinary ZipFile successfully reads the reviewed row, ignoring the secret.
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read(zipped.namelist()[0]).startswith(b'{')
    with pytest.raises(handoff.HandoffError, match='compressed trailing'):
        handoff.audit_handoff(source)


def test_round_reports_duplicate_submissions_and_objective_coverage(tmp_path):
    case = handoff._synthetic_case(0)
    reordered = json.loads(canonical_request(case['source_selection_request']))
    reordered['input']['sentence_ids'].reverse()
    duplicate = make_case(reordered, 'different-request-id-does-not-mean-new-case', review=handoff.REVIEW)
    assert duplicate['source_selection_request_sha256'] == case['source_selection_request_sha256']
    additions = tmp_path / 'additions.jsonl'
    additions.write_text(canonical_request(case) + '\n' + canonical_request(duplicate) + '\n')
    report = handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, append_reviewed_jsonl=additions)
    assert report['submitted_additions'] == 2
    assert report['unique_additions'] == report['distinct_cases_added'] == 1
    assert report['duplicate_additions'] == 1
    assert report['case_coverage']['unique_cases'] == report['case_coverage']['distinct_sources'] == 1
    assert report['case_coverage']['selected_sentence_count_buckets']['2-4'] == 1
    assert report['failed_generations'] == 0


def test_raw_and_archived_duplicate_identity_fails_before_corpus_traversal(tmp_path):
    import zipfile
    source = tmp_path / 'input'
    case = handoff._synthetic_case(0)
    store = SummaryCorpus(source)
    store.ingest_request(case['source_selection_request'], case['request_id'], review=case['review'])
    store.archive()
    archive = next((source / 'archives').glob('*.zip'))
    with zipfile.ZipFile(archive) as zipped:
        (source / 'shards' / (archive.stem + '.jsonl')).write_bytes(zipped.read(zipped.namelist()[0]))
    with patch.object(handoff.SummaryCorpus, 'check', side_effect=AssertionError('must reject physical ambiguity first')):
        with pytest.raises(handoff.HandoffError, match='Both raw and archived'):
            handoff.audit_handoff(source)
        with pytest.raises(handoff.HandoffError, match='Both raw and archived'):
            handoff.run_round(out=tmp_path / 'out', source_sha=SOURCE_SHA, input_corpus=source)
    assert not (tmp_path / 'out').exists()
