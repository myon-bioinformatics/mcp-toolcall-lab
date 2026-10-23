"""Portable contract tests need only stdlib Python + Node, not MCP or a model."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from mcp_toolcall_lab.adapters.engine_adapter import distill_engine_adapter, export_adapter_cache, extract_engine_spec
from mcp_toolcall_lab.portable_execution import CONTRACT, execute, request_sha256

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'src/mcp_toolcall_lab/static/portable_cli.mjs'
CORE = ROOT / 'src/mcp_toolcall_lab/static/portable_execution.mjs'
ENV = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHON': sys.executable}


def request(operation='trace_summary', **data):
    return {'schema': CONTRACT, 'operation': operation, 'input': data or {'jsonl': ''}}


def node(value, *args, raw=False):
    proc = subprocess.run(['node', str(CLI), *map(str, args)], input=value if raw else json.dumps(value, ensure_ascii=False), text=True, capture_output=True, env=ENV, check=True, timeout=20)
    return json.loads(proc.stdout)


def stable(value):
    value = json.loads(json.dumps(value))
    if 'adapter_receipt' in value:
        value['adapter_receipt'].pop('execution_ms', None)
    return value


class PortableExecutionTests(unittest.TestCase):
    def test_one_shared_log_analyzer_and_hash_parity(self):
        text = '\n'.join(json.dumps(x, ensure_ascii=False) for x in [
            {'event': 'tools/call', 'outcome': 'success', 'tool': '日本語🧪'},
            {'event': 'tools/call', 'outcome': 'empty'},
            {'event': 'tools/call', 'outcome': 'error'},
            {'event': 'tools/call', 'outcome': 'new-outcome'},
            {'event': 'ironmate-client/tools/call', 'outcome': 'success'},
            {'event': 'portable/execution'},
        ]) + '\n'
        value = request(jsonl=text)
        result = execute(value)
        self.assertEqual(result, node(value))
        self.assertEqual(result['metrics'], {'records': 6, 'tool_calls': 4, 'success': 1, 'empty': 1, 'error': 1, 'unknown_outcome': 1, 'ignored_events': 2})
        self.assertEqual(result['provenance']['input_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(result['provenance']['request_sha256'], request_sha256(value))
        self.assertEqual([a['line'] for a in result['annotations']], [1, 2, 3, 4])
        self.assertIsNone(result['confidence'])
        self.assertFalse(result['model_inference_performed'])

    def test_recorded_confidence_is_separate_and_attributed(self):
        text = '\n'.join(json.dumps(x) for x in [
            {'event': 'jev/backend_answer', 'answer': {'type': 'noul', 'noul': 0.8}, 'prob_source': 'fixture'},
            {'event': 'jev/backend_answer', 'answer': {'type': 'score', 'confidence': 0.6}, 'prob_source': 'unverified-caller'},
            {'event': 'jev/backend_answer', 'answer': {'type': 'choice', 'confidence': 0.9}},
            {'event': 'jev/backend_answer', 'answer': {'type': 'noul', 'noul': True}, 'prob_source': 'fixture'},
        ])
        result = execute(request(jsonl=text))
        self.assertIsNone(result['confidence'])
        self.assertEqual([r['line'] for r in result['recorded_confidence']], [1, 2])
        self.assertEqual(result['recorded_confidence'][0]['prob_source'], 'fixture')
        self.assertEqual(result['recorded_confidence'][0]['field'], 'noul')
        self.assertTrue(all(not r['calibrated'] for r in result['recorded_confidence']))
        self.assertEqual(len(result['annotations']), 2)

    def test_source_selection_uses_same_node_core_and_stale_ids_fail_closed(self):
        text = 'Graphs connect nodes. Graphs support routing. 原文を選択します。'
        value = request('source_selection', text=text, keywords=[], sentence_ids=[])
        initial = execute(value)
        self.assertEqual(initial, node(value))
        self.assertEqual(initial['selection_status'], 'none_selected')
        value['input']['sentence_ids'] = [initial['sentences'][0]['id']]
        selected = execute(value)
        self.assertEqual(selected, node(value))
        self.assertFalse(selected['model_inference_performed'])
        self.assertIsNone(selected['confidence'])
        self.assertIsNone(selected['summary_request']['generated_text'])
        self.assertEqual(selected['summary_request']['runtime_status'], 'build_time_only')
        self.assertEqual(selected['provenance']['request_sha256'], request_sha256(value))
        value['input']['text'] += ' New source.'
        self.assertEqual(execute(value)['status'], 'unsupported')
        self.assertEqual(execute(value, native_only=True)['error']['code'], 'runtime_unavailable')

    def test_summary_directory_uses_shared_verifier_in_both_clis(self):
        from mcp_toolcall_lab.portable_summary_build import build_summaries
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'artifacts'
            manifest = build_summaries(out, source_sha='a' * 40)
            value = manifest['sources'][0]['choices'][0]['source_selection_request']
            left = execute(value, summary_dir=out)
            right = node(value, '--summary-dir', out)
            self.assertEqual(left, right)
            self.assertEqual(left['status'], 'not_generated')
            self.assertIsNone(left['generated_text'])
            self.assertFalse(left['model_inference_performed'])
            missing = request('source_selection', text='A new local source.', keywords=[], sentence_ids=[])
            missing['input']['sentence_ids'] = [execute(missing)['sentences'][0]['id']]
            self.assertEqual(execute(missing, summary_dir=out), node(missing, '--summary-dir', out))
            self.assertEqual(execute(missing, summary_dir=out)['status'], 'cache_miss')
            self.assertEqual(execute(request(), summary_dir=out)['error']['code'], 'invalid_input')
            # Out-of-band paths cannot make the local fetcher follow symlinks.
            original = out / 'source-summary-manifest.json'
            outside = Path(directory) / 'outside.json'
            original.rename(outside)
            original.symlink_to(outside)
            self.assertEqual(execute(value, summary_dir=out)['error']['code'], 'artifact_unavailable')
            self.assertEqual(execute(value, summary_dir=out), node(value, '--summary-dir', out))

    def test_summary_cli_rejects_missing_or_conflicting_options(self):
        value = request('source_selection', text='Source.', keywords=[], sentence_ids=[])
        for args in [('--summary-dir',), ('--summary-dir', '--native-only'), ('--summary-dir', '.', '--summary-dir', '.')]:
            self.assertEqual(node(value, *args)['error']['code'], 'invalid_input')
        self.assertEqual(execute(value, summary_dir='.', adapter_path='x')['error']['code'], 'invalid_input')

    def test_empty_logs_are_zero_observations_not_confidence(self):
        result = execute(request(jsonl=' \n\n'))
        self.assertEqual(result['metrics']['records'], 0)
        self.assertIsNone(result['confidence'])

    def test_invalid_requests_fail_closed_in_both_hosts(self):
        values = [None, [], {}, {'schema': CONTRACT, 'operation': 'trace_summary', 'input': {'jsonl': ''}, 'ignored': 1},
                  request('unknown', jsonl=''), request(jsonl=4), request(jsonl='not json'), request(jsonl='{}\n[]'),
                  request('adapter_lookup', selector={'query': 'a', 'position': 'b'}), request('adapter_lookup', selector={'query': ''}),
                  request('adapter_lookup', selector={'query': False}), request('adapter_lookup', selector={'prompt': 'infer'}),
                  {'schema': CONTRACT, 'operation': [], 'input': {}},
                  {'schema': CONTRACT, 'operation': {}, 'input': {}}]
        for value in values:
            with self.subTest(value=value):
                left, right = execute(value), node(value)
                self.assertEqual(left['status'], 'unsupported')
                self.assertEqual(left['status'], right['status'])
                self.assertEqual(left['error']['code'], right['error']['code'])
                self.assertIsNone(left['metrics'])
                self.assertIsNone(right['metrics'])

    def test_invalid_jsonl_never_returns_partial_metrics(self):
        result = execute(request(jsonl='{"event":"tools/call","outcome":"success"}\nBAD'))
        self.assertEqual(result['error']['code'], 'invalid_jsonl')
        self.assertIn('Line 2', result['error']['message'])
        self.assertIsNone(result['metrics'])

    def test_input_budgets(self):
        for value in [request(jsonl='x' * (1024 * 1024 + 1)), request(jsonl='{}\n' * 10001), request(jsonl='\n'.join(['{}'] * 10001)), request('adapter_lookup', selector={'query': 'あ' * 1366})]:
            self.assertEqual(execute(value)['error']['code'], 'invalid_input')
            self.assertEqual(node(value)['error']['code'], 'invalid_input')
        self.assertEqual(node('x' * (2 * 1024 * 1024 + 1), raw=True)['error']['code'], 'invalid_input')

    def test_unicode_split_input_and_lone_surrogate_rejection(self):
        value = request(jsonl='{"text":"😀日本語"}\n')
        self.assertEqual(execute(value), node(value))
        raw = json.dumps(request(jsonl='\ud800'))
        self.assertEqual(node(raw, raw=True)['error']['code'], 'invalid_input')
        self.assertEqual(execute(request(jsonl='\ud800'))['error']['code'], 'invalid_input')

    def test_escaped_surrogates_in_log_fields_are_rejected(self):
        for row in [{'event': 'jev/backend_answer', 'answer': {'type': 'noul', 'noul': 0.8}, 'prob_source': '\ud800'}, {'nested': {'\udfff': 4}}]:
            value = request(jsonl=json.dumps(row))
            self.assertEqual(execute(value)['error']['code'], 'invalid_jsonl')
            self.assertEqual(node(value)['error']['code'], 'invalid_jsonl')

    def test_malformed_utf8_is_rejected_by_both_clis(self):
        raw = b'{"schema":"lab-execution/1","operation":"trace_summary","input":{"jsonl":"\xff"}}'
        for command in [['node', str(CLI)], [sys.executable, '-S', '-m', 'mcp_toolcall_lab.portable_execution']]:
            proc = subprocess.run(command, input=raw, capture_output=True, env=ENV, check=True)
            self.assertEqual(json.loads(proc.stdout)['error']['code'], 'invalid_input')

    def test_python_rejects_non_utf8_and_excessive_json_depth(self):
        values = [b'\xef\xbb\xbf' + json.dumps(request()).encode(), json.dumps(request()).encode('utf-16'), json.dumps(request()).encode('utf-32'), ('[' * 10000 + '0' + ']' * 10000).encode()]
        for raw in values:
            proc = subprocess.run([sys.executable, '-S', '-m', 'mcp_toolcall_lab.portable_execution'], input=raw, capture_output=True, env=ENV, check=True)
            self.assertEqual(json.loads(proc.stdout)['error']['code'], 'invalid_input')

    def test_adapter_reuses_python_contract_and_node_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'adapter.json'
            adapter = distill_engine_adapter(extract_engine_spec(engine_name='test', source_type='local', repo_or_image='fixture'), {'known': {'metric': 0, 'confidence': None}, 'default': False})
            export_adapter_cache(adapter, cache)
            for selector in [{'query': 'known'}, {'position': 'known'}, {}, {'query': 'unseen'}]:
                value = request('adapter_lookup', selector=selector)
                left, right = execute(value, adapter_path=cache), node(value, '--adapter', cache)
                self.assertEqual(stable(left), stable(right))
                self.assertFalse(left['adapter_receipt']['model_inference_performed'])
                self.assertTrue(left['adapter_receipt']['upstream_bypassed'])
            missing = execute(request('adapter_lookup', selector={'query': 'unseen'}), adapter_path=cache)
            self.assertEqual(missing['status'], 'cache_miss')
            self.assertIsNone(missing['adapter_receipt']['evaluation'])
            data = json.loads(cache.read_text()); data['evaluation_features']['known'] = 'tampered'; cache.write_text(json.dumps(data))
            self.assertEqual(execute(request('adapter_lookup', selector={}), adapter_path=cache)['error']['code'], 'invalid_adapter')
            self.assertEqual(node(request('adapter_lookup', selector={}), '--adapter', cache)['error']['code'], 'invalid_adapter')

    def test_native_only_leaf_calls_do_not_recurse(self):
        self.assertEqual(execute(request(jsonl=''), native_only=True)['error']['code'], 'runtime_unavailable')
        self.assertEqual(node(request('adapter_lookup', selector={}), '--native-only')['error']['code'], 'runtime_unavailable')

    def test_receipt_fetch_exact_match_miss_and_mismatch(self):
        script = '''import {fetchBuildReceipt, canonicalRequest, sha256} from CORE;
const req={schema:'lab-execution/1',operation:'adapter_lookup',input:{selector:{query:'known'}}};
const key=await sha256(canonicalRequest(req));
const record={request:req,request_sha256:key,receipt:{schema:'lab-execution/1',status:'ok',execution_mode:'cached_observation',model_inference_performed:false},build:{source_sha:'a'.repeat(40)}};
const ok=await fetchBuildReceipt(req,'https://example.test/execution/',async url=>{if(!String(url).endsWith(key+'.json'))throw Error('identity');return {ok:true,status:200,json:async()=>record}});
const miss=await fetchBuildReceipt(req,'https://example.test/execution/',async()=>({status:404}));
record.request_sha256='wrong';
const mismatch=await fetchBuildReceipt(req,'https://example.test/execution/',async()=>({ok:true,json:async()=>record}));
const offline=await fetchBuildReceipt(req,'https://example.test/execution/',async()=>{throw Error('offline')});
console.log(JSON.stringify([ok,miss,mismatch,offline]));'''.replace('CORE', json.dumps(CORE.as_uri()))
        rows = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], text=True))
        self.assertEqual(rows[0]['delivery_mode'], 'build_artifact')
        self.assertEqual(rows[1]['status'], 'cache_miss')
        self.assertEqual(rows[2]['error']['code'], 'artifact_mismatch')
        self.assertEqual(rows[3]['error']['code'], 'artifact_unavailable')

    def test_shared_ledger_does_not_copy_raw_input(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'audit.jsonl'
            value = request(jsonl='{"secret":"do-not-log"}')
            result = subprocess.run([sys.executable, '-S', '-m', 'mcp_toolcall_lab.portable_execution'], input=json.dumps(value), text=True, capture_output=True, check=True, env={**ENV, 'MCP_TOOLCALL_LOG': str(log)})
            self.assertEqual(json.loads(result.stdout)['status'], 'ok')
            row = json.loads(log.read_text())
            self.assertEqual(row['event'], 'portable/execution')
            self.assertNotIn('do-not-log', log.read_text())
            self.assertEqual(row['provenance']['request_sha256'], request_sha256(value))

    def test_export_standalone_runtime_and_source_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'site'
            sha = 'a' * 40
            subprocess.run([sys.executable, '-S', str(ROOT / 'scripts/build_portable_pages.py'), '--out', str(out), '--source-sha', sha], env=ENV, check=True)
            target = out / 'execution'
            self.assertIn('location.replace', (out / 'index.html').read_text())
            meta = json.loads((target / 'build.json').read_text())
            self.assertEqual(meta['source_sha'], sha)
            self.assertEqual(meta['summaries']['status'], 'not_generated')
            self.assertFalse(meta['summaries']['model_inference_performed'])
            manifest = json.loads((target / 'source-summary-manifest.json').read_text())
            self.assertEqual(len(manifest['entries']), 12)
            self.assertEqual(len(manifest['sources']), 4)
            self.assertTrue(all(entry['status'] == 'not_generated' and entry['receipt'] is None for entry in manifest['entries'].values()))
            self.assertFalse((target / 'summaries').exists())
            self.assertFalse(any(path.suffix == '.gguf' for path in target.rglob('*')))
            example = [json.loads(line) for line in (target / 'example.jsonl').read_text().splitlines()]
            self.assertEqual(example[0]['arguments'], {'query': 'Exampleville'})
            self.assertEqual(example[0]['result'], [{'code': '90001', 'name': 'Exampleville', 'prefecture': 'Example Region'}])
            for filename, digest in meta['assets'].items():
                self.assertEqual(hashlib.sha256((target / filename).read_bytes()).hexdigest(), digest)
            self.assertEqual((target / 'runtime/src/mcp_toolcall_lab/static/portable_execution.mjs').read_bytes(), CORE.read_bytes())
            extracted = Path(directory) / 'extracted'
            with zipfile.ZipFile(target / 'runtime.zip') as archive:
                archive.extractall(extracted)
            proc = subprocess.run([sys.executable, '-S', '-m', 'mcp_toolcall_lab.portable_execution'], input=(target / 'example-request.json').read_text(), text=True, capture_output=True, check=True, cwd=extracted, env={**os.environ, 'PYTHONPATH': str(extracted / 'src')})
            self.assertEqual(json.loads(proc.stdout)['metrics']['tool_calls'], 2)
            self.assertTrue((extracted / 'LICENSE').is_file())
            self.assertTrue((extracted / 'vendor.lock.json').is_file())
            again = subprocess.run([sys.executable, '-S', str(ROOT / 'scripts/build_portable_pages.py'), '--out', str(out), '--source-sha', sha], env=ENV, capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn(b'stale build receipts', again.stderr)

    def test_only_one_pages_publisher(self):
        workflows = list((ROOT / '.github/workflows').glob('*.yml'))
        publishers = [p.name for p in workflows if 'uses: actions/deploy-pages@' in p.read_text()]
        self.assertEqual(publishers, ['portable-pages.yml'])
        docker = (ROOT / 'docker/portable-pages/Dockerfile').read_text()
        self.assertIn('FROM scratch AS artifact', docker)
        self.assertIn('ENV LD_LIBRARY_PATH=/app', docker)
        self.assertLess(docker.index('RUN /app/llama-server --version'), docker.index('RUN python3 -S scripts/fetch_tiny_cpu_gguf.py'))
        self.assertNotIn('CMD', docker)

    def test_export_rejects_private_output_overlap_or_preexisting_files(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'site'
            command = [sys.executable, '-S', str(ROOT / 'scripts/build_portable_pages.py'), '--out', str(out), '--source-sha', 'a' * 40]
            for flag in ('--reviewed-corpus', '--result-cache', '--model', '--llama-server'):
                result = subprocess.run([*command, flag, str(out / 'private')], env=ENV, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'outside the public output', result.stderr)
                self.assertFalse(out.exists())
            out.mkdir()
            (out / 'private.txt').write_text('Unpublished input')
            result = subprocess.run(command, env=ENV, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'fresh empty directory', result.stderr)
            self.assertEqual((out / 'private.txt').read_text(), 'Unpublished input')
            self.assertFalse((out / 'execution').exists())


if __name__ == '__main__':
    unittest.main()
