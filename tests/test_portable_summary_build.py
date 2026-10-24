"""Offline contracts only: mocked completions are not real model execution evidence."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from mcp_toolcall_lab import portable_summary_build as summary
from mcp_toolcall_lab.portable_execution import canonical_request, execute, request_sha256
from mcp_toolcall_lab.portable_summary_corpus import (
    SummaryCorpus, make_case, normalize_selection_request, summarize_case_coverage,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = 'a' * 40
TEST_RUNTIME = {'version_output': 'version: 11046 (60081bb2)\nSYNTHETIC UNIT TEST ONLY', 'binary_sha256': 'f' * 64, 'verification': 'Owned local process; version/revision checked and executable bytes hashed. The version string alone is not producer authentication.', 'observed_default_top_k': 40}
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
            for version in [b'version: 11045 (60081bb2)', b'version: 11046 (deadbeef)',
                            b'version: 11046 (60081bbdeadbeef)', b'anything']:
                completed = subprocess.CompletedProcess([], 0, version, b'')
                with patch.object(summary.subprocess, 'run', return_value=completed):
                    with self.assertRaisesRegex(summary.SummaryBuildError, 'version/revision'):
                        summary.verify_runtime(binary)
            completed = subprocess.CompletedProcess([], 0, b'', b'version: 11046 (60081bb2)\nbuilt with test\n')
            with patch.object(summary.subprocess, 'run', return_value=completed):
                observed = summary.verify_runtime(binary)
                self.assertEqual(observed['binary_sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_runtime_version_matches_actual_pinned_format_and_entire_commit_prefix(self):
        actual = 'version: 0.4.1-dev (build 11046, commit 60081bb2b)'
        accepted = [actual, actual + '\nbuilt with compiler details\n',
            'version: 11046 (60081bb2)', 'version: b11046 (60081bb)',
            f'version: 0.4.1-dev (build 11046, commit {summary.RUNTIME["revision"]})']
        rejected = [actual.replace('build 11046', 'build 11045'),
            actual.replace('60081bb2b', 'deadbeef0'),
            actual.replace('60081bb2b', '60081bb2deadbeef'),
            actual.replace('60081bb2b', '60081bb2b0'),  # matching prefix, wrong extension
            actual.replace('60081bb2b', '60081b'),
            actual.replace('60081bb2b', summary.RUNTIME['revision'] + '0'),
            actual.replace('build 11046', 'build 011046'),
            actual + ' trailing text', actual + '\n' + accepted[2],
            'version: 11046 (60081bbdeadbeef)', 'version: 11046 (60081bb2b0)',
            actual.replace('0.4.1-dev', 'not-a-version'), 'prefix ' + actual]
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
        self.assertEqual(summary.RUNTIME['version'], image['version'])
        self.assertEqual(summary.RUNTIME['image'], image['image'] + ':server@' + image['digest'])

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


if __name__ == '__main__':
    unittest.main()
