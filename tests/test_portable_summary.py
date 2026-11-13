"""Portable summary build, corpus, fetch and incremental handoff: offline contracts, no exported generation."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab import portable_summary_build as summary, portable_summary_corpus as corpus
from mcp_toolcall_lab.portable_execution import canonical_request, execute, request_sha256
from mcp_toolcall_lab.portable_summary_corpus import SummaryCorpus, make_case, normalize_selection_request, summarize_case_coverage


# ═══ · portable summary build ════════════════════════════════════════════════════════════════════════════
# Offline contracts only: mocked completions are not real model execution evidence.

SOURCE_SHA = 'a' * 40
TEST_RUNTIME = {'version_output': 'version: 11669 (620d2225)\nSYNTHETIC UNIT TEST ONLY', 'binary_sha256': 'f' * 64, 'verification': 'Owned local process; version/revision checked and executable bytes hashed. The version string alone is not producer authentication.', 'observed_default_top_k': 40}
REVIEW = {'classification': 'reviewed_public', 'reviewed_by': 'unit-test reviewer', 'reviewed_at': '2026-10-23T00:00:00+00:00'}


def fake_response(text='Offline unit-test response, never a public fixture.'):
    return {'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 50, 'completion_tokens': 10, 'total_tokens': 60}}


def fetch_static_in_node(directory, requests):
    """Actual browser verifier + local fixture transport; no browser/network."""
    core = (ROOT / 'src/mcp_toolcall_lab/static/portable_execution.mjs').as_uri()
    script = '''import {readFileSync} from 'node:fs'; import {join} from 'node:path';
import {fetchSourceSummary} from CORE;
const input=JSON.parse(readFileSync(0,'utf8'));
const fetcher=async url=>{try{return new Response(readFileSync(join(input.directory,new URL(url).pathname.slice('/execution/'.length))),{status:200});}
catch{return new Response('',{status:404});}};
const results=[];for(const request of input.requests)results.push(await fetchSourceSummary(request,'https://example.invalid/execution/',fetcher));
console.log(JSON.stringify(results));'''.replace('CORE', json.dumps(core))
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script],
        input=json.dumps({'directory': str(directory), 'requests': requests}), text=True, timeout=20))


class PortableSummaryBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = summary.declared_sources()
        cls.selection = cls.sources[0]['choices'][1]['source_selection_request']
        cls.case_count = sum(len(source['choices']) for source in cls.sources)

    def test_fixture_is_pinned_bounded_and_synthetic(self):
        self.assertEqual(hashlib.sha256(summary.FIXTURE.read_bytes()).hexdigest(), summary.FIXTURE_SHA256)
        self.assertEqual(len(self.sources), 4)
        self.assertEqual(self.case_count, 12)
        self.assertLessEqual(self.case_count, summary.MAX_CHOICES)
        self.assertEqual(summary.MAX_CHOICES, 16)
        self.assertEqual(len(self.sources[0]['choices']), 4)
        for source in self.sources:
            self.assertLessEqual(len(source['text'].encode()), summary.MAX_PUBLIC_SOURCE_BYTES)
            for choice in source['choices']:
                request = choice['source_selection_request']
                self.assertEqual(choice['source_selection_request_sha256'], request_sha256(request))
                self.assertEqual(execute(request)['selection_status'], 'ready')
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / 'private.json'
            fixture.write_text(json.dumps({'sources': [{'text': 'Private text.'}]}))
            with self.assertRaisesRegex(summary.SummaryBuildError, 'checksum'):
                summary.declared_sources(fixture)

    def test_seed_cases_are_meaningfully_distinct_and_canonical(self):
        cases = []
        semantic_keys = set()
        for source in self.sources:
            for choice in source['choices']:
                request = choice['source_selection_request']
                self.assertEqual(request, normalize_selection_request(request))
                case = make_case(request, choice['id'])
                self.assertEqual(case['source_selection_request_sha256'], choice['source_selection_request_sha256'])
                semantic_keys.add((case['source_sha256'], frozenset(request['input']['keywords']),
                                   frozenset(request['input']['sentence_ids'])))
                cases.append(case)
        self.assertEqual(len(semantic_keys), 12)
        self.assertEqual(len({source['source_sha256'] for source in self.sources}), 4)
        coverage = summarize_case_coverage(iter(cases))
        self.assertEqual((coverage['discovered_cases'], coverage['unique_cases'], coverage['duplicate_cases'],
                          coverage['distinct_sources']), (12, 12, 0, 4))
        self.assertEqual(coverage['keyword_count_buckets'], {'0': 5, '1': 5, '2-4': 2, '5-8': 0, '9+': 0})
        self.assertEqual(coverage['selected_sentence_count_buckets'], {'1': 2, '2-4': 9, '5-8': 1, '9+': 0})
        # Permutations/caller labels are not what makes this fixture broader.
        with tempfile.TemporaryDirectory() as temporary:
            store = SummaryCorpus(temporary)
            for index, case in enumerate(cases):
                request = case['source_selection_request']
                store.ingest_request(request, f'original-{index}')
                reordered = copy.deepcopy(request)
                reordered['input']['keywords'].reverse()
                reordered['input']['sentence_ids'].reverse()
                store.ingest_request(reordered, f'renamed-{index}')
            self.assertEqual(store.check()['cases'], 12)

    def test_original_research_source_and_four_choices_remain_compatible(self):
        original = self.sources[0]
        self.assertEqual(original['id'], 'research-archive')
        self.assertEqual(original['source_sha256'], 'f37eec719848c47b5c64fb171c5690556d07a3f5a3329807b540010fa1a64037')
        self.assertEqual([(choice['id'], choice['source_selection_request_sha256']) for choice in original['choices']], [
            ('overview', '4deeae35e7a20b261cb3b85441814363ef730d93131630f2a776f3706c9db6f1'),
            ('evidence', '683107a558b80796dff223c8e8601f03f83bc9791d36929dbb654e8993a67616'),
            ('summary', '03ce499c11402927c4aea2991b5337d83acad32a896fe78a527c00f58bd776ec'),
            ('archive', '8903965066bf9f8c195184c4e97533b9f9fc6422b99947eea0f4bde3451849ae'),
        ])

    def test_exact_request_binds_source_keywords_evidence_and_sampler(self):
        selection = copy.deepcopy(self.selection)
        resolved = summary.prepare_summary_request(selection)
        before = canonical_request(resolved)
        selection['input']['text'] += ' changed'
        self.assertEqual(canonical_request(resolved), before)
        self.assertEqual(resolved['source_selection_request_sha256'], request_sha256(self.selection))
        selected = execute(self.selection)
        self.assertEqual(resolved['selected_source_evidence'], selected['selected_sentences'])
        self.assertEqual(resolved['source_sha256'], selected['provenance']['input_sha256'])
        self.assertEqual(resolved['selected_keyword_ids'], self.selection['input']['keywords'])
        self.assertEqual(resolved['sampler'], {'temperature': 0, 'seed': 42, 'top_p': .75, 'repeat_penalty': 1, 'top_k': 40})
        self.assertIs(type(resolved['sampler']['top_k']), int)
        key = summary.summary_request_sha256(resolved)
        for path, value in [(['source_sha256'], '0' * 64), (['sampler', 'temperature'], 1),
                            (['sampler', 'top_k'], .75), (['model', 'sha256'], 'f' * 64),
                            (['runtime', 'revision'], '0' * 40), (['system_prompt'], 'Changed prompt')]:
            changed = copy.deepcopy(resolved)
            pointer = changed
            for field in path[:-1]:
                pointer = pointer[field]
            pointer[path[-1]] = value
            self.assertNotEqual(summary.summary_request_sha256(changed), key)
        changed = copy.deepcopy(resolved)
        changed['selected_source_evidence'][0]['text'] += ' changed'
        self.assertNotEqual(summary.summary_request_sha256(changed), key)
        changed = copy.deepcopy(self.selection)
        changed['input']['keywords'] = []
        self.assertNotEqual(summary.summary_request_sha256(summary.prepare_summary_request(changed)), key)

    def test_summary_hash_agrees_with_canonical_javascript(self):
        request = summary.prepare_summary_request(self.selection)
        # Independent recursive canonicalization detects array-object hash drift.
        script = '''import {readFileSync} from 'node:fs'; import {createHash} from 'node:crypto';
const sort = v => Array.isArray(v) ? v.map(sort) : v && typeof v==='object'
  ? Object.fromEntries(Object.keys(v).sort().map(k=>[k,sort(v[k])])) : v;
console.log(createHash('sha256').update(JSON.stringify(sort(JSON.parse(readFileSync(0,'utf8'))))).digest('hex'));'''
        actual = subprocess.check_output(['node', '--input-type=module', '-e', script], input=json.dumps(request), text=True).strip()
        self.assertEqual(actual, summary.summary_request_sha256(request))

    def test_source_edits_or_stale_ids_fail_before_generation(self):
        for change in ['text', 'sentence_ids']:
            value = copy.deepcopy(self.selection)
            if change == 'text':
                value['input']['text'] += ' changed'
            else:
                value['input']['sentence_ids'] = []
            with self.assertRaises(summary.SummaryBuildError):
                summary.prepare_summary_request(value)
        with self.assertRaises(summary.SummaryBuildError):
            summary.prepare_summary_request({'schema': 'lab-execution/1', 'operation': 'adapter_lookup', 'input': {}})
        for invalid in [None, [], 'request', {'value': float('nan')}]:
            with self.assertRaises(summary.SummaryBuildError):
                summary.prepare_summary_request(invalid)

    def test_no_runtime_produces_only_honest_manifest_without_calls(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(summary, 'owned_server') as server:
            out = Path(temporary)
            manifest = summary.build_summaries(out, source_sha=SOURCE_SHA)
            server.assert_not_called()
            self.assertEqual(manifest['status'], 'not_generated')
            self.assertFalse(manifest['model_inference_performed'])
            self.assertFalse((out / 'summaries').exists())
            self.assertEqual([p.name for p in out.iterdir()], ['source-summary-manifest.json'])
            self.assertEqual(manifest, json.loads((out / 'source-summary-manifest.json').read_text()))
            for entry in manifest['entries'].values():
                self.assertEqual(entry['status'], 'not_generated')
                self.assertIsNone(entry['receipt'])
            self.assertNotIn('generated_text', json.dumps(manifest))
            with self.assertRaisesRegex(summary.SummaryBuildError, 'fresh'):
                summary.build_summaries(out, source_sha=SOURCE_SHA)

    def test_no_one_sided_runtime_or_bad_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            for kwargs in [{'model_path': Path('missing')}, {'llama_server': Path('missing')}]:
                with self.assertRaisesRegex(summary.SummaryBuildError, 'Both'):
                    summary.build_summaries(Path(temporary), source_sha=SOURCE_SHA, **kwargs)
            for sha in ['', 'HEAD', 'g' * 40, 'a' * 39]:
                with self.assertRaisesRegex(summary.SummaryBuildError, '40-character'):
                    summary.build_summaries(Path(temporary), source_sha=sha)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_corpus_and_cache_cannot_enter_public_output_even_through_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'public'
            output.mkdir()
            (output / 'known-static-asset.txt').write_text('Existing public build asset.')
            alias = root / 'cache-alias'
            alias.symlink_to(output / 'private-cache', target_is_directory=True)
            for field, candidate in [('result_cache', output), ('result_cache', output / 'cache'),
                                     ('reviewed_corpus', output / 'cases.jsonl'), ('result_cache', alias),
                                     ('reviewed_corpus', alias / 'cases.jsonl')]:
                with self.subTest(field=field, path=candidate), self.assertRaisesRegex(summary.SummaryBuildError, 'outside the public'):
                    summary.build_summaries(output, source_sha=SOURCE_SHA, **{field: candidate})
                self.assertEqual([path.name for path in output.iterdir()], ['known-static-asset.txt'])
                self.assertFalse((output / 'private-cache').exists())
            for model, binary in [(output / 'model.gguf', root / 'llama-server'),
                                  (root / 'model.gguf', output / 'llama-server')]:
                with self.assertRaisesRegex(summary.SummaryBuildError, 'outside the public'):
                    summary.build_summaries(output, source_sha=SOURCE_SHA, model_path=model, llama_server=binary)
            self.assertEqual([path.name for path in output.iterdir()], ['known-static-asset.txt'])
            # The build exporter may already have copied known public assets.
            summary.build_summaries(output, source_sha=SOURCE_SHA)
            self.assertTrue((output / 'source-summary-manifest.json').exists())

    def test_nonloopback_redirect_proxy_and_endpoint_restrictions(self):
        for url in ['https://127.0.0.1:8080', 'http://localhost:8080', 'http://example.com:80',
                    'http://127.0.0.1:8080/', 'http://127.0.0.1:8080?x=1', 'http://user@127.0.0.1:8080',
                    'http://127.0.0.1', 'http://[::1]:8080']:
            with self.subTest(url=url), self.assertRaises(summary.SummaryBuildError):
                summary.LocalClient(url)
        client = summary.LocalClient('http://127.0.0.1:12345')
        self.assertFalse(any(isinstance(handler, summary.urllib.request.ProxyHandler) and handler.proxies
                             for handler in client.opener.handlers))
        with self.assertRaisesRegex(summary.SummaryBuildError, 'redirect'):
            summary._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://outside.invalid')
        with self.assertRaisesRegex(summary.SummaryBuildError, 'Unexpected'):
            client.request('/arbitrary', {})

    def test_model_pin_and_runtime_version_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / 'model.gguf'
            model.write_bytes(b'not model weights')
            with self.assertRaisesRegex(summary.SummaryBuildError, 'size'):
                summary.verify_model(model)
            with patch.dict(summary.MODEL, {'size_bytes': model.stat().st_size}):
                with self.assertRaisesRegex(summary.SummaryBuildError, 'SHA-256'):
                    summary.verify_model(model)
            binary = Path(temporary) / 'llama-server'
            binary.write_bytes(b'unit-test placeholder, never executed')
            for version in [b'version: 11668 (620d2225)', b'version: 11669 (deadbeef)',
                            b'version: 11669 (620d222deadbeef)', b'anything']:
                completed = subprocess.CompletedProcess([], 0, version, b'')
                with patch.object(summary.subprocess, 'run', return_value=completed):
                    with self.assertRaisesRegex(summary.SummaryBuildError, 'version/revision'):
                        summary.verify_runtime(binary)
            completed = subprocess.CompletedProcess([], 0, b'', b'version: 11669 (620d2225)\nbuilt with test\n')
            with patch.object(summary.subprocess, 'run', return_value=completed):
                observed = summary.verify_runtime(binary)
                self.assertEqual(observed['binary_sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_runtime_version_matches_actual_pinned_format_and_entire_commit_prefix(self):
        actual = 'version: 0.7.0 (build 11669, commit 620d2225c)'
        accepted = [actual, actual + '\nbuilt with compiler details\n',
            'version: 11669 (620d2225)', 'version: b11669 (620d222)',
            f'version: 0.7.0 (build 11669, commit {summary.RUNTIME["revision"]})']
        rejected = [actual.replace('build 11669', 'build 11668'),
            actual.replace('620d2225c', 'deadbeef0'),
            actual.replace('620d2225c', '620d2225deadbeef'),
            actual.replace('620d2225c', '620d2225c0'),  # matching prefix, wrong extension
            actual.replace('620d2225c', '620d22'),
            actual.replace('620d2225c', summary.RUNTIME['revision'] + '0'),
            actual.replace('build 11669', 'build 011669'),
            actual + ' trailing text', actual + '\n' + accepted[2],
            'version: 11669 (620d222deadbeef)', 'version: 11669 (620d2225c0)',
            actual.replace('0.7.0', 'not-a-version'), 'prefix ' + actual]
        for output in accepted:
            with self.subTest(accepted=output):
                self.assertTrue(summary.runtime_version_matches(output, summary.RUNTIME))
        for output in rejected:
            with self.subTest(rejected=output):
                self.assertFalse(summary.runtime_version_matches(output, summary.RUNTIME))
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'llama-server'
            binary.write_bytes(b'TEST ONLY: no runtime execution')
            result = subprocess.CompletedProcess([], 0, b'', actual.encode())
            with patch.object(summary.subprocess, 'run', return_value=result):
                observed = summary.verify_runtime(binary)
            self.assertEqual(observed['version_output'], actual)
            self.assertEqual(observed['binary_sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_runtime_preflight_failure_reports_bounded_stderr_without_input_or_stdout(self):
        binary = Path('/unit-test/llama-server')
        failure = subprocess.CalledProcessError(127, [str(binary), '--version'],
            output=b'PRIVATE STDOUT MUST NOT BE REPORTED',
            stderr=b'X' * 6000 + b'\n\x1b[31mllama-server: error while loading shared libraries: libllama-server-impl.so: cannot open shared object file: No such file or directory\x1b[0m\n')
        with patch.object(summary.subprocess, 'run', side_effect=failure) as run:
            with self.assertRaises(summary.SummaryBuildError) as observed:
                summary.verify_runtime(binary)
        message = str(observed.exception)
        self.assertIn('exit code 127', message)
        self.assertIn('libllama-server-impl.so', message)
        self.assertIn('pre-inference --version stderr tail', message)
        self.assertNotIn('PRIVATE STDOUT', message)
        self.assertNotIn('\x1b', message)
        self.assertLess(len(message), 2300)
        self.assertEqual(run.call_args.args[0], [str(binary), '--version'])
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        self.assertTrue(run.call_args.kwargs['check'])
        for error, expected in [(OSError(2, 'missing', '/private/not-a-source'), 'OS error 2'),
                                (subprocess.TimeoutExpired([str(binary), '--version'], 15), '15-second timeout')]:
            with patch.object(summary.subprocess, 'run', side_effect=error):
                with self.assertRaises(summary.SummaryBuildError) as caught:
                    summary.verify_runtime(binary)
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn('/private/', str(caught.exception))

    def test_wire_has_only_selected_evidence_no_tools_or_external_url(self):
        request = summary.prepare_summary_request(self.selection)
        body = summary.completion_body(request)
        self.assertEqual(body['model'], summary.MODEL_ALIAS)
        self.assertFalse(body['stream'])
        self.assertFalse(body['cache_prompt'])
        self.assertNotIn('tools', body)
        self.assertEqual(body['messages'][0]['role'], 'system')
        self.assertIn('untrusted', body['messages'][0]['content'])
        for sentence in request['selected_source_evidence']:
            self.assertIn(sentence['text'], body['messages'][1]['content'])
        self.assertNotIn('The demo archive stores', body['messages'][1]['content'])
        for top_k in [.75, 40.0, True, None]:
            invalid = copy.deepcopy(request)
            invalid['sampler']['top_k'] = top_k
            with self.assertRaisesRegex(summary.SummaryBuildError, 'integer'):
                summary.completion_body(invalid)

    def test_invalid_completions_fail_closed_and_truncation_stays_visible(self):
        valid = fake_response()
        bad = [{}, {'choices': []}, copy.deepcopy(valid), copy.deepcopy(valid), copy.deepcopy(valid),
               copy.deepcopy(valid), copy.deepcopy(valid), copy.deepcopy(valid), copy.deepcopy(valid)]
        bad[2]['choices'][0]['message']['content'] = ''
        bad[3]['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'external'}}]
        bad[4]['usage']['completion_tokens'] = True
        bad[5]['usage']['total_tokens'] = 999
        bad[6]['choices'][0]['finish_reason'] = 'tool_calls'
        bad[7]['choices'][0]['message']['role'] = 'user'
        bad[8]['choices'][0]['message']['content'] = '\ud800'
        for value in bad:
            with self.subTest(value=value), self.assertRaises(summary.SummaryBuildError):
                summary._observed_completion(value)
        valid['choices'][0]['finish_reason'] = 'length'
        self.assertEqual(summary._observed_completion(valid)[2], 'length')

    def test_offline_mock_proves_artifact_contract_not_live_generation(self):
        client = Mock()
        client.request.return_value = fake_response()
        @contextmanager
        def fake_server(*args):
            yield client, copy.deepcopy(TEST_RUNTIME)
        with tempfile.TemporaryDirectory() as temporary, patch.object(summary, 'owned_server', fake_server):
            out = Path(temporary)
            manifest = summary.build_summaries(out, source_sha=SOURCE_SHA, model_path=Path('unit-test-only'), llama_server=Path('unit-test-only'))
            self.assertEqual(client.request.call_count, self.case_count)
            log_bytes = (out / 'summary-build.jsonl').read_bytes()
            log_hash = hashlib.sha256(log_bytes).hexdigest()
            rows = [json.loads(line) for line in log_bytes.splitlines()]
            self.assertEqual(len(rows), self.case_count)
            self.assertEqual(manifest['observation_log'], {'path': 'summary-build.jsonl', 'sha256': log_hash, 'lines': self.case_count})
            self.assertNotIn(self.sources[0]['text'], log_bytes.decode())
            self.assertNotIn('Offline unit-test response', log_bytes.decode())
            self.assertNotIn('tools/call', log_bytes.decode())
            for sentence in execute(self.selection)['sentences']:
                self.assertNotIn(sentence['text'], log_bytes.decode())
            for selection_key, entry in manifest['entries'].items():
                artifact = json.loads((out / entry['receipt']).read_text())
                self.assertEqual(artifact['source_selection_request_sha256'], selection_key)
                self.assertEqual(request_sha256(artifact['source_selection_request']), selection_key)
                self.assertEqual(summary.summary_request_sha256(artifact['summary_request']), entry['summary_request_sha256'])
                self.assertEqual(artifact['receipt']['usage']['completion_tokens'], 10)
                self.assertEqual(artifact['receipt']['generated_text'], fake_response()['choices'][0]['message']['content'])
                self.assertEqual(artifact['receipt']['wire_request_sha256'], request_sha256(summary.completion_body(artifact['summary_request'])))
                provenance = artifact['receipt']['observation_log']
                original_log_bytes = (out / provenance['path']).read_bytes()
                self.assertEqual(provenance['sha256'], hashlib.sha256(original_log_bytes).hexdigest())
                line = original_log_bytes.splitlines()[provenance['line'] - 1]
                self.assertEqual(provenance['line_sha256'], hashlib.sha256(line).hexdigest())
                observed = json.loads(line)
                self.assertEqual(observed['event'], 'portable/summary_generation')
                self.assertEqual(observed['source_selection_request_sha256'], selection_key)
                self.assertEqual(observed['summary_request_sha256'], artifact['summary_request_sha256'])
                self.assertEqual(observed['usage'], artifact['receipt']['usage'])
                self.assertEqual(observed['elapsed_ms'], artifact['receipt']['elapsed_ms'])
                self.assertEqual(observed['generated_text_sha256'], hashlib.sha256(artifact['receipt']['generated_text'].encode()).hexdigest())
                self.assertIsNone(artifact['receipt']['confidence'])
                self.assertIn('not guarantee', artifact['receipt']['determinism_note'])
                self.assertNotIn('model_path', artifact['receipt'])
            self.assertEqual(len(list((out / 'summaries').iterdir())), self.case_count)

    def test_partial_generation_failure_never_publishes_receipts(self):
        client = Mock()
        client.request.side_effect = [fake_response(), summary.SummaryBuildError('failed')]
        @contextmanager
        def fake_server(*args):
            yield client, copy.deepcopy(TEST_RUNTIME)
        with tempfile.TemporaryDirectory() as temporary, patch.object(summary, 'owned_server', fake_server):
            out = Path(temporary)
            with self.assertRaisesRegex(summary.SummaryBuildError, 'failed'):
                summary.build_summaries(out, source_sha=SOURCE_SHA, model_path=Path('unit-test-only'), llama_server=Path('unit-test-only'))
            self.assertEqual(list(out.iterdir()), [])

    def test_runtime_pins_match_existing_model_and_image_provenance(self):
        model = json.loads((ROOT / 'docker/stub-pages/models/model.gguf.provenance.json').read_text())
        image = json.loads((ROOT / 'docker/stub-pages/llama.cpp.image.provenance.json').read_text())
        for field in ['repo', 'revision', 'file', 'sha256', 'size_bytes']:
            self.assertEqual(summary.MODEL[field], model[field])
        self.assertEqual(summary.RUNTIME['revision'], image['revision'])
        self.assertEqual('v0.7.0 (' + summary.RUNTIME['version'] + ')', image['version'])
        self.assertEqual(summary.RUNTIME['image'], image['image'] + ':' + image['tag'] + '@' + image['digest'])

    def test_verified_cache_reuse_after_archive_never_launches_model(self):
        client = Mock()
        client.request.return_value = fake_response()
        @contextmanager
        def fake_server(*args):
            yield client, copy.deepcopy(TEST_RUNTIME)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache_path = root / 'cache'
            with patch.object(summary, 'owned_server', fake_server):
                first = summary.build_summaries(root / 'first', source_sha=SOURCE_SHA,
                    model_path=Path('unit-test-only'), llama_server=Path('unit-test-only'), result_cache=cache_path)
            self.assertEqual(client.request.call_count, self.case_count)
            self.assertEqual(first['new_generations'], self.case_count)
            store = SummaryCorpus(cache_path)
            store.archive()
            self.assertEqual(store.check()['results'], self.case_count)
            with patch.object(summary, 'owned_server') as server:
                second = summary.build_summaries(root / 'second', source_sha='b' * 40,
                    model_path=Path('missing-but-unused'), llama_server=Path('missing-but-unused'), result_cache=cache_path)
                server.assert_not_called()
            self.assertEqual(second['reused_results'], self.case_count)
            self.assertEqual(second['new_generations'], 0)
            self.assertFalse(second['model_inference_performed'])
            self.assertIsNone(second['observation_log'])
            self.assertFalse((root / 'second/summary-build.jsonl').exists())
            for entry in second['entries'].values():
                self.assertTrue(entry['reused'])
                self.assertEqual(entry['artifact_build']['source_sha'], SOURCE_SHA)
                original = (root / 'first' / entry['receipt']).read_bytes()
                reused = (root / 'second' / entry['receipt']).read_bytes()
                self.assertEqual(original, reused)
                artifact = json.loads(reused)
                log_path = artifact['receipt']['observation_log']['path']
                self.assertEqual((root / 'first' / log_path).read_bytes(), (root / 'second' / log_path).read_bytes())
            browser_results = fetch_static_in_node(root / 'second', [choice['source_selection_request'] for source in self.sources for choice in source['choices']])
            self.assertTrue(all(result['status'] == 'generated' for result in browser_results), browser_results)
            self.assertTrue(all(result['reused'] and result['provenance']['observation_log_verified'] for result in browser_results))
            self.assertTrue(all(result['build']['source_sha'] == SOURCE_SHA and result['delivery_build']['source_sha'] == 'b' * 40 for result in browser_results))
            # A changed prompt is a different recipe, never a fuzzy cache hit.
            with patch.object(summary, 'SYSTEM_PROMPT', summary.SYSTEM_PROMPT + ' Changed.'), patch.object(summary, 'owned_server') as server:
                changed = summary.build_summaries(root / 'changed', source_sha='c' * 40, result_cache=cache_path)
                server.assert_not_called()
            self.assertEqual(changed['reused_results'], 0)
            self.assertEqual(changed['status'], 'not_generated')
            self.assertTrue(all(e['receipt'] is None for e in changed['entries'].values()))

    def test_failed_batch_checkpoints_success_and_retry_runs_only_missing(self):
        failed = Mock()
        failed.request.side_effect = [fake_response(), summary.SummaryBuildError('second failed')]
        @contextmanager
        def failed_server(*args):
            yield failed, copy.deepcopy(TEST_RUNTIME)
        retry = Mock()
        retry.request.return_value = fake_response()
        @contextmanager
        def retry_server(*args):
            yield retry, copy.deepcopy(TEST_RUNTIME)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = {'model_path': Path('unit-test-only'), 'llama_server': Path('unit-test-only'), 'result_cache': root / 'cache'}
            with patch.object(summary, 'owned_server', failed_server), self.assertRaisesRegex(summary.SummaryBuildError, 'second failed'):
                summary.build_summaries(root / 'first', source_sha=SOURCE_SHA, **kwargs)
            self.assertEqual(list((root / 'first').iterdir()), [])
            self.assertEqual(SummaryCorpus(root / 'cache').check()['results'], 1)
            with patch.object(summary, 'owned_server') as server:
                partial = summary.build_summaries(root / 'partial', source_sha='b' * 40, result_cache=root / 'cache')
                server.assert_not_called()
            self.assertEqual(partial['status'], 'partially_generated')
            self.assertEqual(partial['reused_results'], 1)
            self.assertEqual(partial['new_generations'], 0)
            self.assertFalse(partial['model_inference_performed'])
            self.assertEqual(sum(e['status'] == 'not_generated' for e in partial['entries'].values()), self.case_count - 1)
            cached_entry = next(e for e in partial['entries'].values() if e['status'] == 'generated')
            self.assertTrue((root / 'partial' / cached_entry['receipt']).is_file())
            browser_results = fetch_static_in_node(root / 'partial', [choice['source_selection_request'] for source in self.sources for choice in source['choices']])
            self.assertEqual(sum(result['status'] == 'generated' for result in browser_results), 1, browser_results)
            self.assertEqual(sum(result['status'] == 'not_generated' for result in browser_results), self.case_count - 1, browser_results)
            with patch.object(summary, 'owned_server', retry_server):
                second = summary.build_summaries(root / 'retry', source_sha='b' * 40, **kwargs)
            self.assertEqual(retry.request.call_count, self.case_count - 1)
            self.assertEqual(second['reused_results'], 1)
            self.assertEqual(second['new_generations'], self.case_count - 1)
            self.assertEqual(len((root / 'retry/summary-build.jsonl').read_text().splitlines()), self.case_count - 1)
            self.assertEqual(SummaryCorpus(root / 'cache').check()['results'], self.case_count)

    def test_owned_server_cleanup_and_no_environment_recipe_override(self):
        binary, model = Path('/unit-test/llama-server'), Path('/unit-test/model.gguf')
        process = Mock()
        process.poll.return_value = None
        client = Mock()
        client.request.side_effect = [{'status': 'ok'}, {'model_path': str(model), 'default_generation_settings': {'params': {'top_k': 40}}}]
        reservation = Mock()
        reservation.getsockname.return_value = ('127.0.0.1', 12345)
        socket_context = Mock()
        socket_context.__enter__ = Mock(return_value=reservation)
        socket_context.__exit__ = Mock(return_value=False)
        with patch.object(summary, 'verify_model'), patch.object(summary, 'verify_runtime', return_value=copy.deepcopy(TEST_RUNTIME)), \
             patch.object(summary.socket, 'socket', return_value=socket_context), patch.object(summary, 'LocalClient', return_value=client), \
             patch.object(summary.subprocess, 'Popen', return_value=process) as launch, \
             patch.dict(summary.os.environ, {'LLAMA_ARG_HOST': '0.0.0.0', 'LLAMA_ARG_MODEL': 'wrong'}):
            with self.assertRaisesRegex(RuntimeError, 'inside'):
                with summary.owned_server(binary, model):
                    raise RuntimeError('inside')
            self.assertNotIn('LLAMA_ARG_HOST', launch.call_args.kwargs['env'])
            self.assertNotIn('LLAMA_ARG_MODEL', launch.call_args.kwargs['env'])
            args = launch.call_args.args[0]
            self.assertEqual(args[args.index('--host') + 1], '127.0.0.1')
            self.assertEqual(args[args.index('--model') + 1], str(model))
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=10)

    def test_reviewed_corpus_pages_deduplicate_and_expose_next_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_path = root / 'reviewed.jsonl'
            records = [make_case(choice['source_selection_request'], choice['id'], review=REVIEW)
                       for choice in self.sources[0]['choices']]
            corpus_path.write_text(''.join(json.dumps(case) + '\n' for case in [records[0], *records]))
            first = summary.build_summaries(root / 'page-1', source_sha=SOURCE_SHA, reviewed_corpus=corpus_path, case_limit=2)
            self.assertEqual(len(first['entries']), 2)
            self.assertEqual(first['corpus_window']['next_offset'], 2)
            self.assertEqual(first['corpus_window']['total_cases'], 4)
            second = summary.build_summaries(root / 'page-2', source_sha=SOURCE_SHA, reviewed_corpus=corpus_path,
                                             case_offset=2, case_limit=2)
            self.assertEqual(len(second['entries']), 2)
            self.assertIsNone(second['corpus_window']['next_offset'])
            self.assertFalse(set(first['entries']) & set(second['entries']))
            self.assertEqual(set(first['entries']) | set(second['entries']), {case['source_selection_request_sha256'] for case in records})


# ═══ · portable summary corpus ═══════════════════════════════════════════════════════════════════════════
# Synthetic offline contracts only. No mocked output is exported as generation.

PORTABLE_SUMMARY_CORPUS_REVIEW = {"classification": "reviewed_public", "reviewed_by": "offline test reviewer",
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
    prepared = summary.prepare_summary_request(selection_request)
    source_key, key = request_sha256(selection_request), request_sha256(prepared)
    text = "SYNTHETIC UNIT TEST ONLY: no model was run."
    observed = {"schema": "lab-summary-observation/1", "event": "portable/summary_generation",
                "model_inference_performed": True, "source_selection_request_sha256": source_key,
                "summary_request_sha256": key, "wire_request_sha256": request_sha256(summary.completion_body(prepared)),
                "generated_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                "elapsed_ms": 1, "finish_reason": "stop", "generated_at": "2026-10-23T00:00:00+00:00"}
    raw = (canonical_request(observed) + "\n").encode()
    log_key = hashlib.sha256(raw).hexdigest()
    receipt = {**observed, "schema": "lab-summary-receipt/1", "status": "generated",
               "execution_mode": "build_time_llama_cpp", "confidence": None,
               "generated_text": text, "truncated": False, "runtime_observed": {"version_output": "version: 0.7.0 (build 11669, commit 620d2225c)\nSYNTHETIC UNIT TEST ONLY",
                   "binary_sha256": "f" * 64, "observed_default_top_k": 40,
                   "verification": "Owned local process; version/revision checked and executable bytes hashed. The version string alone is not producer authentication."},
               "confidence_reason": "Uncalibrated model output; no probability of correctness is claimed.",
               "determinism_note": summary.DETERMINISM_NOTE,
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
    for review in [{}, {**PORTABLE_SUMMARY_CORPUS_REVIEW, "classification": "private"}, {**PORTABLE_SUMMARY_CORPUS_REVIEW, "reviewed_at": "2026-10-23"}, {**PORTABLE_SUMMARY_CORPUS_REVIEW, "extra": True}]:
        with pytest.raises(corpus.CorpusError):
            store.ingest_request(selection_request, "review", review=review)
    case = store.ingest_request(selection_request, "private")
    with pytest.raises(corpus.CorpusError, match="immutable review"):
        store.ingest_request(selection_request, "public", review=PORTABLE_SUMMARY_CORPUS_REVIEW)
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
    case = corpus.make_case(selection_request, "public-round-1", review=PORTABLE_SUMMARY_CORPUS_REVIEW)
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
    with patch.object(summary, "owned_server", side_effect=AssertionError("No model may run")):
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
    cases = [corpus.make_case(selection(f"Synthetic reviewed case {number}. New evidence."), f"round-{number}", review=PORTABLE_SUMMARY_CORPUS_REVIEW)
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
    new = corpus.make_case(selection("A newly accumulated source. Additional evidence."), "round-4", review=PORTABLE_SUMMARY_CORPUS_REVIEW)
    with path.open("a") as handle: handle.write(canonical_request(new) + "\n")
    _, changed = corpus.load_reviewed_corpus_window(path, case_limit=2)
    assert changed["total_cases"] == 4 and changed["corpus_sha256"] != window["corpus_sha256"]
    for kwargs in [{"case_offset": 99}, {"case_offset": True}, {"case_limit": 0}, {"case_limit": 257}]:
        with pytest.raises(corpus.CorpusError): corpus.load_reviewed_corpus_window(path, **kwargs)


def test_window_validates_unselected_records_before_public_projection(tmp_path, selection_request):
    public = corpus.make_case(selection_request, "public", review=PORTABLE_SUMMARY_CORPUS_REVIEW)
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
    canonical_summary = summary.prepare_summary_request(selected)
    original_summary = summary.prepare_summary_request(reversed_order)
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
    "version: 0.7.0 (build 11669, commit 620d2225c)",
    "version: 11669 (620d2225)",
    "version: b11669 (620d222)",
    f"version: 0.7.0 (build 11669, commit {summary.RUNTIME['revision']})",
])
def test_runtime_observation_accepts_recorded_build_and_entire_revision(selection_request, version):
    artifact, raw = synthetic_result(selection_request)
    artifact['receipt']['runtime_observed']['version_output'] = version
    assert corpus.validate_summary_artifact(artifact, log_bytes=raw) == artifact


@pytest.mark.parametrize("version", [
    "version: 0.7.0 (build 11668, commit 620d2225c)",
    "version: 0.7.0 (build 11669, commit deadbeef0)",
    "version: 0.7.0 (build 11669, commit 620d2225c0)",
    "version: 0.7.0 (build 11669, commit 620d222deadbeef)",
    "version: 11669 (620d222deadbeef)",
    "version: 11669 (620d2225c0)",
    f"version: 0.7.0 (build 11669, commit {summary.RUNTIME['revision']}0)",
    "version: 11669 (620d2225)\nversion: 11668 (620d2225)",
])
def test_runtime_observation_rejects_wrong_build_or_commit_extension(selection_request, version):
    artifact, raw = synthetic_result(selection_request)
    artifact['receipt']['runtime_observed']['version_output'] = version
    with pytest.raises(corpus.CorpusError, match='version/revision'):
        corpus.validate_summary_artifact(artifact, log_bytes=raw)


def test_artifact_validator_uses_shared_runtime_matcher(selection_request):
    artifact, raw = synthetic_result(selection_request)
    with patch.object(summary, 'runtime_version_matches', return_value=False) as matcher:
        with pytest.raises(corpus.CorpusError, match='version/revision'):
            corpus.validate_summary_artifact(artifact, log_bytes=raw)
    matcher.assert_called_once_with(artifact['receipt']['runtime_observed']['version_output'], artifact['summary_request']['runtime'])


# ═══ · portable summary fetch ════════════════════════════════════════════════════════════════════════════
# Static-summary verification only. Synthetic test stubs are never exported.

CORE = ROOT / 'src/mcp_toolcall_lab/static/portable_execution.mjs'
SCRIPT = '''
import {readFileSync} from 'node:fs';
import {canonicalRequest, sha256, executeSource, fetchSourceSummary} from CORE;
const configuration = JSON.parse(readFileSync(0, 'utf8'));
if (configuration.canonical !== undefined) {
  const text = canonicalRequest(configuration.canonical);
  console.log(JSON.stringify({canonical:text, hash:await sha256(text)}));
} else {
  const outcomes = [];
  for (const scenario of configuration.scenarios) {
    delete globalThis.location;
    const recipe = {
      model:{repo:'unit-test/stub',revision:'a'.repeat(40),file:'not-a-real-model.gguf',sha256:'a'.repeat(64),size_bytes:1,license:'TEST ONLY'},
      runtime:{engine:'llama.cpp',version:'b1',revision:'b'.repeat(40),image:'ghcr.io/ggml-org/llama.cpp:server-b11669@sha256:'+'b'.repeat(64),license:'TEST ONLY'},
      sampler:{temperature:0,seed:42,top_p:0.75,repeat_penalty:1,top_k:40},
      generation:{max_tokens:128,stream:false,cache_prompt:false},
      server:{context_size:2048,parallel:1,threads:1,batch_size:64,gpu_layers:0},
      prompt_version:'unit-test-stub/1',system_prompt:'UNIT TEST ONLY; no model is invoked.',
      top_k_source:'https://example.invalid/unit-test-only',remaining_defaults:'Synthetic unit-test recipe, never an exported artifact.',
    };
    const request = {schema:'lab-execution/1',operation:'source_selection',input:{text:'😀 Selected evidence. Unselected evidence.',keywords:[],sentence_ids:[]}};
    const initial = await executeSource(request);
    request.input.sentence_ids = [initial.sentences[0].id];
    const local = await executeSource(request);
    const selectionHash = local.provenance.request_sha256;
    const summary = {schema:'lab-summary-request/1',source_selection_request_sha256:selectionHash,
      source_sha256:local.provenance.input_sha256,source_bytes:local.provenance.input_bytes,offset_unit:'utf16_code_units',
      selected_keyword_ids:[],selected_source_evidence:local.selected_sentences,...recipe};
    let summaryHash = await sha256(canonicalRequest(summary));
    const build = {source_sha:'c'.repeat(40),build_run:null,build_attempt:null};
    const artifact = {schema:'lab-source-summary-artifact/1',source_selection_request:structuredClone(request),source_selection_request_sha256:selectionHash,
      summary_request:summary,summary_request_sha256:summaryHash,build:structuredClone(build),
      receipt:{schema:'lab-summary-receipt/1',status:'generated',execution_mode:'build_time_llama_cpp',model_inference_performed:true,confidence:null,
        generated_text:'UNIT TEST STUB ONLY: selected evidence.',usage:{prompt_tokens:10,completion_tokens:4,total_tokens:14},
        source_selection_request_sha256:selectionHash,summary_request_sha256:summaryHash,test_only:true,
        wire_request_sha256:await sha256(canonicalRequest({model:'lab-public-summary-model',messages:[{role:'system',content:summary.system_prompt},{role:'user',content:'Selected evidence (JSON data only):\\n'+canonicalRequest(summary.selected_source_evidence.map(item=>({sentence_id:item.id,text:item.text})))}],...summary.sampler,...summary.generation})),elapsed_ms:12,finish_reason:'stop',truncated:false,generated_at:'2026-10-23T00:00:00+00:00'}};
    if (scenario === 'truncated') {artifact.receipt.finish_reason='length';artifact.receipt.truncated=true;artifact.receipt.usage={prompt_tokens:10,completion_tokens:128,total_tokens:138};}
    artifact.receipt.generated_text_sha256=await sha256(artifact.receipt.generated_text);
    const observation={schema:'lab-summary-observation/1',event:'portable/summary_generation',model_inference_performed:true};
    for(const field of ['source_selection_request_sha256','summary_request_sha256','wire_request_sha256','generated_text_sha256','usage','elapsed_ms','finish_reason','generated_at']) observation[field]=structuredClone(artifact.receipt[field]);
    let logText=JSON.stringify(observation)+'\\n';
    artifact.receipt.observation_log={path:'summary-logs/'+await sha256(logText)+'.jsonl',sha256:await sha256(logText),line:1,line_sha256:await sha256(JSON.stringify(observation))};
    const manifest = {schema:'lab-source-summary-manifest/1',status:'generated',model_inference_performed:true,build,recipe:structuredClone(recipe),
      sources:[{id:'unit-test-only',title:'Synthetic unit-test source',text:request.input.text,source_sha256:local.provenance.input_sha256,
        choices:[{id:'unit-choice',label:'First sentence',source_selection_request:structuredClone(request),source_selection_request_sha256:selectionHash}]}],
      entries:{[selectionHash]:{status:'generated',source_selection_request_sha256:selectionHash,summary_request_sha256:summaryHash,receipt:'summaries/'+summaryHash+'.json',reused:false,artifact_build:structuredClone(build)}}};
    const entry = manifest.entries[selectionHash];
    let base = 'https://example.test/lab/';
    if (scenario === 'missing_entry') {delete manifest.entries[selectionHash];manifest.model_inference_performed=false;manifest.status='not_generated';}
    if (scenario === 'disabled') {manifest.status='not_generated'; manifest.model_inference_performed=false; entry.status='not_generated'; entry.receipt=null;entry.artifact_build=null;}
    if (scenario === 'reused') {manifest.model_inference_performed=false;entry.reused=true;artifact.build.source_sha='d'.repeat(40);entry.artifact_build=structuredClone(artifact.build);}
    if (scenario === 'partial') {manifest.status='partially_generated';manifest.model_inference_performed=false;entry.reused=true;manifest.entries['f'.repeat(64)]={status:'not_generated',source_selection_request_sha256:'f'.repeat(64),summary_request_sha256:'d'.repeat(64),receipt:null,reused:false,artifact_build:null};}
    if (scenario === 'wire_rehashed') {artifact.receipt.wire_request_sha256='f'.repeat(64);observation.wire_request_sha256='f'.repeat(64);logText=JSON.stringify(observation)+'\\n';artifact.receipt.observation_log={path:'summary-logs/'+await sha256(logText)+'.jsonl',sha256:await sha256(logText),line:1,line_sha256:await sha256(JSON.stringify(observation))};}
    if (scenario === 'no_selected') request.input.sentence_ids=[];
    if (scenario === 'stale') request.input.text+=' Changed.';
    if (scenario === 'unknown_selection') request.input.sentence_ids=[initial.sentences[1].id];
    if (scenario === 'bad_manifest_schema') manifest.schema='other';
    if (scenario === 'bad_manifest_flag') manifest.model_inference_performed=false;
    if (scenario === 'bad_artifact_schema') artifact.schema='other';
    if (scenario === 'bad_build') artifact.build.source_sha='d'.repeat(40);
    if (scenario === 'bad_declaration_source') manifest.sources[0].source_sha256='f'.repeat(64);
    if (scenario === 'bad_declaration_text') manifest.sources[0].text+=' forged';
    if (scenario === 'bad_declaration_choice') manifest.sources[0].choices[0].source_selection_request.input.text+=' forged';
    if (scenario === 'duplicate_choice') manifest.sources[0].choices.push(structuredClone(manifest.sources[0].choices[0]));
    if (scenario === 'bad_selection_wrapper') artifact.source_selection_request.input.text+=' forged';
    if (scenario === 'bad_sampler') manifest.recipe.sampler.top_p=0.8;
    if (scenario === 'bad_runtime') manifest.recipe.runtime.engine='remote-api';
    if (scenario === 'bad_model') manifest.recipe.model.sha256='not-a-hash';
    if (scenario === 'bad_summary_hash') {entry.summary_request_sha256='e'.repeat(64); entry.receipt='summaries/'+entry.summary_request_sha256+'.json';}
    if (scenario.startsWith('unsafe:')) entry.receipt=scenario.slice(7);
    if (scenario.startsWith('receipt:')) {
      const [field, value] = JSON.parse(scenario.slice(8)); artifact.receipt[field]=value;
    }
    if (scenario === 'unicode') artifact.bad='\\ud800';
    if (scenario === 'deep') {let next=manifest; for(let i=0;i<35;i++) {next.extra={}; next=next.extra;}}
    if (scenario === 'nodes') manifest.extra=Array(100001).fill(0);
    if (scenario === 'forged_evidence') artifact.summary_request.selected_source_evidence[0].text='Forged evidence.';
    if (scenario === 'forged_offsets') artifact.summary_request.selected_source_evidence[0].start=1;
    if (scenario === 'forged_source') artifact.summary_request.source_sha256='f'.repeat(64);
    if (scenario.startsWith('forged_')) {
      summaryHash=await sha256(canonicalRequest(artifact.summary_request));
      artifact.summary_request_sha256=summaryHash; artifact.receipt.summary_request_sha256=summaryHash;
      entry.summary_request_sha256=summaryHash; entry.receipt='summaries/'+summaryHash+'.json';
    }
    if (scenario === 'bad_text_checksum') artifact.receipt.generated_text_sha256='0'.repeat(64);
    if (scenario === 'wrong_log_line') artifact.receipt.observation_log.line=2;
    if (scenario === 'bad_log_line_checksum') artifact.receipt.observation_log.line_sha256='0'.repeat(64);
    if (scenario === 'tampered_log') logText+=' ';
    if (scenario === 'unsafe_log_path') artifact.receipt.observation_log.path='https://other.invalid/log';
    if (scenario === 'wrong_log_usage') observation.usage.total_tokens=19;
    if (scenario === 'wrong_log_duration') observation.elapsed_ms=999;
    if (scenario === 'wrong_log_event') observation.event='tools/call';
    if (scenario === 'raw_data_log') observation.text='Raw source must never be included.';
    if (scenario.startsWith('wrong_log_') && scenario !== 'wrong_log_line' || scenario === 'raw_data_log') {
      logText=JSON.stringify(observation)+'\\n';
      artifact.receipt.observation_log={path:'summary-logs/'+await sha256(logText)+'.jsonl',sha256:await sha256(logText),line:1,line_sha256:await sha256(JSON.stringify(observation))};
    }
    if (scenario === 'cross_origin') {globalThis.location={href:'https://example.test/lab/index.html',origin:'https://example.test'};base='https://other.invalid/';}
    if (scenario === 'bad_protocol') base='file:///tmp/private/';
    const calls=[];
    const fetcher = async (url, options) => {
      calls.push({url:String(url),method:options.method,body:options.body??null,redirect:options.redirect,mode:options.mode,credentials:options.credentials});
      const first=String(url).endsWith('/source-summary-manifest.json');
      const logRequest=String(url).includes('/summary-logs/');
      if (scenario === 'snapshot' && first) {request.input.text='changed after await';request.input.keywords.push('invalid');request.input.sentence_ids.push('invalid');}
      if (scenario === 'network_error') throw Error('network unavailable');
      if ((scenario === 'manifest_404' && first) || (scenario === 'artifact_404' && !first)) return new Response('',{status:404});
      if (scenario === 'missing_log' && logRequest) return new Response('',{status:404});
      if (scenario === 'oversized_log' && logRequest) return new Response(' '.repeat(1024*1024+1));
      if (scenario === 'http_error') return new Response('',{status:503});
      if (scenario === 'bad_json') return new Response('{broken');
      if (scenario === 'invalid_utf8') return new Response(new Uint8Array([0xff]));
      if (scenario === 'nonfinite') return new Response('{"bad":1e999}');
      if (scenario === 'oversized_header') return new Response('{}',{headers:{'content-length':String(2*1024*1024+1)}});
      if (scenario === 'oversized_body') return new Response(' '.repeat(2*1024*1024+1));
      const response = new Response(logRequest?logText:JSON.stringify(first?manifest:artifact),{headers:{'content-type':'application/json'}});
      if (scenario === 'redirected') Object.defineProperty(response,'redirected',{value:true});
      if (scenario === 'wrong_response_url') Object.defineProperty(response,'url',{value:'https://other.invalid/forged.json'});
      return response;
    };
    const result=await fetchSourceSummary(request,base,fetcher);
    const repeated=scenario==='repeated'?await fetchSourceSummary(request,base,fetcher):null;
    outcomes.push({scenario,result,repeated,calls,summaryHash,selectionHash,summaryCanonical:canonicalRequest(summary),logText,
      fixture:scenario==='local_fixture'?{request,files:{'source-summary-manifest.json':JSON.stringify(manifest),
        ['summaries/'+summaryHash+'.json']:JSON.stringify(artifact),[artifact.receipt.observation_log.path]:logText}}:null});
  }
  console.log(JSON.stringify(outcomes));
}
'''.replace('CORE', json.dumps(CORE.as_uri()))


def node(payload):
    completed = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], input=json.dumps(payload),
                               text=True, capture_output=True, timeout=30, check=True)
    return json.loads(completed.stdout)


class PortableSummaryFetchTests(unittest.TestCase):
    def assert_unavailable(self, result, code=None):
        self.assertNotEqual(result['status'], 'generated')
        self.assertIsNone(result['generated_text'])
        self.assertIsNone(result['confidence'])
        self.assertFalse(result['model_inference_performed'])
        self.assertNotIn('summary_request', result)
        if code:
            self.assertEqual(result['error']['code'], code)

    def test_recursive_arrays_canonical_hash_matches_python(self):
        value = {'z': [{'z': '😀日本語', 'a': [{'b': 2, 'a': 1}, None, True]}],
                 'a': {'z': 0.75, 'a': [{'c': 3, 'b': 2, 'a': 1}]}}
        result = node({'canonical': value})
        expected = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual(result['canonical'], expected)
        self.assertEqual(result['hash'], hashlib.sha256(expected.encode()).hexdigest())

    def test_exact_generated_receipt_with_synthetic_unit_stub_only(self):
        row = node({'scenarios': ['ready']})[0]
        result = row['result']
        self.assertEqual(result['status'], 'generated')
        self.assertEqual(result['generated_text'], 'UNIT TEST STUB ONLY: selected evidence.')
        self.assertTrue(result['model_inference_performed'])
        self.assertIsNone(result['confidence'])
        self.assertEqual(result['delivery_mode'], 'build_artifact')
        self.assertEqual(result['provenance']['source_selection_request_sha256'], row['selectionHash'])
        self.assertEqual(result['provenance']['summary_request_sha256'], row['summaryHash'])
        self.assertNotIn('Unselected', json.dumps(result))
        self.assertEqual(len(row['calls']), 3)
        self.assertEqual(row['calls'][0]['url'], 'https://example.test/lab/source-summary-manifest.json')
        self.assertEqual(row['calls'][1]['url'], f"https://example.test/lab/summaries/{row['summaryHash']}.json")
        self.assertTrue(row['calls'][2]['url'].startswith('https://example.test/lab/summary-logs/'))
        self.assertTrue(result['provenance']['observation_log_verified'])
        self.assertNotIn('Selected evidence', row['logText'])
        self.assertNotIn('UNIT TEST STUB ONLY', row['logText'])
        for call in row['calls']:
            self.assertEqual(call['method'], 'GET')
            self.assertIsNone(call['body'])
            self.assertEqual(call['redirect'], 'error')
            self.assertEqual(call['mode'], 'same-origin')
            self.assertEqual(call['credentials'], 'omit')
        canonical = json.dumps(result['summary_request'], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual(canonical, row['summaryCanonical'])
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), row['summaryHash'])

    def test_verified_token_limit_remains_partial_with_unknown_confidence(self):
        result = node({'scenarios': ['truncated']})[0]['result']
        self.assertEqual(result['status'], 'generated')
        self.assertTrue(result['model_inference_performed'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['finish_reason'], 'length')
        self.assertEqual(result['usage']['completion_tokens'], 128)
        self.assertIsNone(result['confidence'])
        self.assertTrue(result['provenance']['observation_log_verified'])

    def test_misses_disabled_build_and_no_selection_do_not_fallback(self):
        expected = {'missing_entry': ('cache_miss', 1), 'unknown_selection': ('cache_miss', 1),
                    'manifest_404': ('cache_miss', 1), 'artifact_404': ('cache_miss', 2),
                    'disabled': ('build_not_generated', 1), 'no_selected': ('no_selected_evidence', 0), 'stale': ('invalid_input', 0)}
        for row in node({'scenarios': list(expected)}):
            with self.subTest(scenario=row['scenario']):
                code, calls = expected[row['scenario']]
                self.assert_unavailable(row['result'], code)
                self.assertEqual(len(row['calls']), calls)

    def test_source_evidence_recipe_build_and_declared_choice_mismatches(self):
        scenarios = ['bad_manifest_schema', 'bad_manifest_flag', 'bad_artifact_schema', 'bad_build',
                     'bad_declaration_source', 'bad_declaration_text', 'bad_declaration_choice', 'duplicate_choice',
                     'bad_selection_wrapper', 'bad_sampler', 'bad_runtime', 'bad_model', 'bad_summary_hash',
                     'forged_evidence', 'forged_offsets', 'forged_source']
        for row in node({'scenarios': scenarios}):
            with self.subTest(scenario=row['scenario']):
                self.assert_unavailable(row['result'], 'artifact_mismatch')

    def test_arbitrary_or_noncanonical_paths_never_fetched(self):
        paths = ['https://other.invalid/steal', '//other.invalid/steal', '../other.json',
                 'summaries/../other.json', 'summaries/%2e%2e/other.json', 'summaries/fake.json',
                 'summaries/' + 'a' * 64 + '.json?source=private', 'summaries/' + 'a' * 64 + '.json#fragment']
        for row in node({'scenarios': ['unsafe:' + path for path in paths]}):
            self.assert_unavailable(row['result'], 'artifact_mismatch')
            self.assertEqual(len(row['calls']), 1)

    def test_receipt_must_explicitly_record_valid_generated_model_result(self):
        changes = [('schema', 'other'), ('status', 'not_generated'), ('execution_mode', 'remote_api'),
                   ('model_inference_performed', False), ('confidence', 0.9), ('generated_text', None),
                   ('generated_text', '  '), ('generated_text', 'x' * 8193),
                   ('source_selection_request_sha256', '0' * 64), ('summary_request_sha256', '0' * 64),
                   ('usage', {'prompt_tokens': True, 'completion_tokens': 4, 'total_tokens': 5}),
                   ('usage', {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 20}),
                   ('usage', {'prompt_tokens': 10, 'completion_tokens': 129, 'total_tokens': 139})]
        for row in node({'scenarios': ['receipt:' + json.dumps(change) for change in changes]}):
            self.assert_unavailable(row['result'], 'artifact_mismatch')

    def test_artifacts_are_bounded_and_fail_closed_on_bad_encoding(self):
        scenarios = ['bad_json', 'invalid_utf8', 'nonfinite', 'unicode', 'deep', 'nodes',
                     'oversized_header', 'oversized_body', 'redirected', 'wrong_response_url']
        for row in node({'scenarios': scenarios}):
            with self.subTest(scenario=row['scenario']):
                self.assert_unavailable(row['result'], 'artifact_mismatch')

    def test_network_failures_and_cross_origin_bases_are_closed(self):
        for row in node({'scenarios': ['network_error', 'http_error', 'cross_origin', 'bad_protocol']}):
            expected = 'artifact_mismatch' if row['scenario'] in {'cross_origin', 'bad_protocol'} else 'artifact_unavailable'
            self.assert_unavailable(row['result'], expected)
            if row['scenario'] in {'cross_origin', 'bad_protocol'}:
                self.assertEqual(row['calls'], [])

    def test_repeated_fetches_and_input_snapshot_are_stable(self):
        rows = node({'scenarios': ['ready', 'repeated', 'snapshot']})
        self.assertEqual(rows[0]['result'], rows[1]['result'])
        self.assertEqual(rows[1]['result'], rows[1]['repeated'])
        self.assertEqual(rows[0]['result'], rows[2]['result'])
        self.assertEqual(len(rows[1]['calls']), 6)

    def test_local_artifacts_and_python_bridge_fail_closed(self):
        fixture = node({'scenarios': ['local_fixture']})[0]['fixture']
        cli = ROOT / 'src/mcp_toolcall_lab/static/portable_cli.mjs'
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
        env.pop('MCP_TOOLCALL_LOG', None)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, content in fixture['files'].items():
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8')
            commands = [
                ['node', str(cli)],
                [sys.executable, '-S', '-m', 'mcp_toolcall_lab.portable_execution'],
            ]

            def run(command, request=None, extra=None):
                completed = subprocess.run(
                    command + ['--summary-dir', directory] + (extra or []),
                    input=json.dumps(request or fixture['request']), text=True,
                    capture_output=True, check=True, timeout=20, env=env,
                )
                return json.loads(completed.stdout)

            for command in commands:
                self.assertEqual(run(command)['status'], 'generated')
                invalid = {**fixture['request'], 'input': {
                    **fixture['request']['input'], 'text': 'changed source',
                }}
                self.assert_unavailable(run(command, invalid), 'invalid_input')
                self.assertEqual(run(command, extra=['--adapter', 'unused'])['error']['code'], 'invalid_input')

            artifact = next(base.glob('summaries/*.json'))
            original = artifact.read_text(encoding='utf-8')
            artifact.write_text('{broken', encoding='utf-8')
            for command in commands:
                self.assert_unavailable(run(command), 'artifact_mismatch')
            artifact.unlink()
            outside = base / 'outside.json'
            outside.write_text(original, encoding='utf-8')
            try:
                artifact.symlink_to(outside)
            except (OSError, NotImplementedError):
                # Windows environments may not grant symlink privileges.
                return
            for command in commands:
                self.assert_unavailable(run(command), 'artifact_unavailable')

    def test_observation_log_integrity_no_raw_data_and_original_build(self):
        scenarios = ['bad_text_checksum', 'wrong_log_line', 'bad_log_line_checksum', 'tampered_log',
                     'wire_rehashed', 'unsafe_log_path', 'wrong_log_usage', 'wrong_log_duration', 'wrong_log_event', 'raw_data_log', 'oversized_log']
        for row in node({'scenarios': scenarios}):
            with self.subTest(scenario=row['scenario']):
                self.assert_unavailable(row['result'], 'artifact_mismatch')
                if row['scenario'] == 'unsafe_log_path':
                    self.assertEqual(len(row['calls']), 2)
        missing, reused, partial = node({'scenarios': ['missing_log', 'reused', 'partial']})
        self.assertEqual(partial['result']['status'], 'generated')
        self.assertTrue(partial['result']['reused'])
        self.assert_unavailable(missing['result'], 'cache_miss')
        self.assertEqual(len(missing['calls']), 3)
        self.assertEqual(reused['result']['status'], 'generated')
        self.assertTrue(reused['result']['reused'])
        self.assertEqual(reused['result']['build']['source_sha'], 'd' * 40)
        self.assertEqual(reused['result']['delivery_build']['source_sha'], 'c' * 40)


# ═══ · incremental summary build ═════════════════════════════════════════════════════════════════════════
# Handoff contracts in real clean Python processes, with explicit offline stubs.
#
# No GGUF, llama.cpp, network or Docker runs here. Stub artifacts stay under pytest
# TemporaryDirectory paths; they are never repository/public fixtures.

INCREMENTAL_SUMMARY_BUILD_SCRIPT = ROOT / 'scripts/check_incremental_summary_build.py'
spec = importlib.util.spec_from_file_location('incremental_check', INCREMENTAL_SUMMARY_BUILD_SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


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
    yield OfflineClient(), {'version_output':'version: 11669 (620d2225)\\nSYNTHETIC UNIT TEST ONLY',
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
    environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'OFFLINE_CALL_LEDGER': str(ledger), 'HANDOFF_HELPER_SCRIPT': str(INCREMENTAL_SUMMARY_BUILD_SCRIPT)}
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
    assert report['rounds'][1]['submitted_additions'] == 2
    assert report['rounds'][1]['distinct_cases_added'] == 1
    assert report['rounds'][1]['duplicate_additions'] == 1
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
    assert report['captured_results'] == report['selected_cases'] == len(cases) == 12
    assert report['missing_results'] == report['new_generations'] == 0
    assert report['model_inference_performed'] is False
    store = SummaryCorpus(captured / 'corpus')
    assert {item['source_selection_request_sha256'] for item in store.iter_cases()} == {case['source_selection_request_sha256'] for case in cases}
    assert all(case['review']['classification'] == 'reviewed_public' for case in store.iter_cases())
    assert not list(captured.rglob('DO-NOT-TRANSFER.txt'))
    assert set(path.name for path in captured.iterdir()) == {'corpus', 'report.json'}
    restored = tmp_path / 'restored'
    result = handoff.run_round(out=restored, source_sha='b' * 40, input_corpus=captured / 'corpus')
    assert result['new_generations'] == 0 and result['reused_results'] == len(cases)
    assert result['model_inference_performed'] is False
    assert len(ledger.read_text().splitlines()) == len(cases)
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
    assert report['captured_results'] == 0 and report['missing_results'] == 12
    assert report['counts_after']['cases'] == 12 and report['counts_after']['results'] == 0
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


if __name__ == "__main__":
    unittest.main()
