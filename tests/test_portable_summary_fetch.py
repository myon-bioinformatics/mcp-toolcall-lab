"""Static-summary verification only. Synthetic test stubs are never exported."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
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
    outcomes.push({scenario,result,repeated,calls,summaryHash,selectionHash,summaryCanonical:canonicalRequest(summary),logText});
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


if __name__ == '__main__':
    unittest.main()
