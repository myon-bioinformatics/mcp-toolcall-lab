/** Portable deterministic execution core. No DOM, Node APIs, network or model. */
import {validateSourceInput, executeSourceSelection} from './portable_source.mjs';
export const CONTRACT = 'lab-execution/1';
export const MAX_LOG_BYTES = 1024 * 1024;
export const MAX_ROWS = 10000;
const encoder = new TextEncoder();
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const keysAre = (value, keys) => object(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
function invalidUnicode(value) {
  const pending = [value];
  while (pending.length) {
    const item = pending.pop();
    if (typeof item === 'string' && /\p{Surrogate}/u.test(item)) return true;
    if (item && typeof item === 'object') {
      for (const [key, child] of Object.entries(item)) {
        if (/\p{Surrogate}/u.test(key)) return true;
        pending.push(child);
      }
    }
  }
  return false;
}
export function validateRequest(request) {
  if (!keysAre(request, ['schema', 'operation', 'input']) || request.schema !== CONTRACT) return 'Expected schema, operation and input only, with schema lab-execution/1.';
  if (request.operation === 'trace_summary') {
    if (!keysAre(request.input, ['jsonl']) || typeof request.input.jsonl !== 'string') return 'trace_summary requires input.jsonl as a string.';
    if (encoder.encode(request.input.jsonl).length > MAX_LOG_BYTES) return 'Log exceeds the 1 MiB UTF-8 input limit.';
    if (request.input.jsonl.split('\n').length > MAX_ROWS + (request.input.jsonl.endsWith('\n') ? 1 : 0)) return 'Log exceeds the 10,000-line limit.';
  } else if (request.operation === 'source_selection') {
    return validateSourceInput(request.input);
  } else if (request.operation === 'adapter_lookup') {
    if (!keysAre(request.input, ['selector']) || !object(request.input.selector)) return 'adapter_lookup requires input.selector.';
    const entries = Object.entries(request.input.selector);
    if (entries.length > 1 || entries.some(([key, value]) => !['query', 'position'].includes(key) || typeof value !== 'string' || value.length === 0 || encoder.encode(value).length > 4096)) return 'Use one nonempty query/position string (max 4096 bytes), or {} for the stored default.';
  } else return 'Unknown operation; no execution was performed.';
  // Unicode-regexp mode treats valid pairs as one code point, lone surrogates as invalid.
  const strings = request.operation === 'trace_summary' ? [request.input.jsonl] : Object.values(request.input.selector);
  if (strings.some(text => /\p{Surrogate}/u.test(text))) return 'Input must contain valid Unicode.';
  return null;
}
export function canonicalRequest(request) {
  const sort = value => Array.isArray(value) ? value.map(sort) : object(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sort(value[key])])) : value;
  return JSON.stringify(sort(request));
}
export async function sha256(text) {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', encoder.encode(text));
  return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
}
export function failure(code, message, status = 'unsupported') {
  return {schema: CONTRACT, status, execution_mode: 'unsupported', model_inference_performed: false, metrics: null, confidence: null, error: {code, message}};
}
export async function executeSource(request) {
  const invalid = validateRequest(request);
  if (invalid) return failure('invalid_input', invalid);
  if (request.operation !== 'source_selection') return failure('invalid_input', 'Expected source_selection operation.');
  return executeSourceSelection(request, {sha256, canonicalRequest, failure});
}
export async function executeTrace(request) {
  const invalid = validateRequest(request);
  if (invalid) return failure('invalid_input', invalid);
  if (request.operation !== 'trace_summary') return failure('runtime_unavailable', 'This runtime does not execute Python adapters.');
  const text = request.input.jsonl;
  const metrics = {records: 0, tool_calls: 0, success: 0, empty: 0, error: 0, unknown_outcome: 0, ignored_events: 0};
  const annotations = [];
  const recordedConfidence = [];
  const lines = text.split('\n');
  for (let index = 0; index < lines.length; index++) {
    if (!lines[index].trim()) continue;
    let row;
    try { row = JSON.parse(lines[index]); } catch { return failure('invalid_jsonl', `Line ${index + 1} is not JSON. No partial metrics returned.`); }
    if (!object(row)) return failure('invalid_jsonl', `Line ${index + 1} must be an object. No partial metrics returned.`);
    if (invalidUnicode(row)) return failure('invalid_jsonl', `Line ${index + 1} contains invalid Unicode. No partial metrics returned.`);
    metrics.records++;
    const line = index + 1;
    if (row.event === 'tools/call') {
      metrics.tool_calls++;
      const outcome = ['success', 'empty', 'error'].includes(row.outcome) ? row.outcome : 'unknown_outcome';
      metrics[outcome]++;
      annotations.push({line, event: 'tools/call', outcome});
    } else {
      metrics.ignored_events++;
      if (row.event === 'jev/backend_answer') {
        const answer = row.answer;
        const field = answer?.type === 'noul' ? 'noul' : ['choice', 'score'].includes(answer?.type) ? 'confidence' : null;
        const value = field ? answer[field] : null;
        if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 && typeof row.prob_source === 'string' && row.prob_source.length) {
          recordedConfidence.push({line, value, field, prob_source: row.prob_source, calibrated: false});
        } else annotations.push({line, event: 'jev/backend_answer', warning: 'No valid recorded probability with prob_source; not inferred.'});
      }
    }
  }
  return {
    schema: CONTRACT, status: 'ok', execution_mode: 'deterministic_log_analysis', model_inference_performed: false,
    metrics, confidence: null, confidence_reason: 'No ground-truth calibration supplied. Counts are observations, not a probability of correctness.',
    recorded_confidence: recordedConfidence, annotations,
    provenance: {input_sha256: await sha256(text), input_bytes: encoder.encode(text).length, request_sha256: await sha256(canonicalRequest(request)), source: 'caller_supplied_jsonl', verification: 'Content identity only; log claims and producer authenticity are not verified.'},
  };
}
/** Fetch an exact build receipt. This is artifact lookup, never remote execution. */
export async function fetchBuildReceipt(request, baseURL, fetcher = globalThis.fetch) {
  const invalid = validateRequest(request);
  if (invalid) return failure('invalid_input', invalid);
  const key = await sha256(canonicalRequest(request));
  try {
    const response = await fetcher(new URL(`results/${key}.json`, baseURL));
    if (response.status === 404) return failure('cache_miss', 'No build receipt for this exact request; Python was not executed.', 'cache_miss');
    if (!response.ok) return failure('artifact_unavailable', `Build receipt fetch failed: HTTP ${response.status}.`);
    const record = await response.json();
    if (!record || canonicalRequest(record.request) !== canonicalRequest(request) || record.request_sha256 !== key || record.receipt?.schema !== CONTRACT) return failure('artifact_mismatch', 'Build receipt does not match the requested input.');
    return {...record.receipt, delivery_mode: 'build_artifact', build: record.build};
  } catch { return failure('artifact_unavailable', 'Could not load the static build receipt.'); }
}

const MAX_SUMMARY_MANIFEST_BYTES = 2 * 1024 * 1024;
const MAX_SUMMARY_ARTIFACT_BYTES = 2 * 1024 * 1024;
const MAX_SUMMARY_LOG_BYTES = 1024 * 1024;
const SHA256 = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const SUMMARY_RECIPE_KEYS = ['model', 'runtime', 'sampler', 'generation', 'server', 'prompt_version', 'system_prompt', 'top_k_source', 'remaining_defaults'];
const SUMMARY_SAMPLER = {temperature: 0, seed: 42, top_p: 0.75, repeat_penalty: 1, top_k: 40};
const same = (left, right) => canonicalRequest(left) === canonicalRequest(right);
function summaryFailure(code, message, status = 'not_generated') {
  return {schema: 'lab-summary-receipt/1', status, execution_mode: 'not_generated', generated_text: null,
    model_inference_performed: false, confidence: null, error: {code, message}};
}
function artifactError(message) {
  const error = new Error(message);
  error.code = 'artifact_mismatch';
  return error;
}
function requireArtifact(condition, message) {
  if (!condition) throw artifactError(message);
}
function boundedJSON(value) {
  const pending = [[value, 0]];
  let visited = 0;
  while (pending.length) {
    const [item, depth] = pending.pop();
    requireArtifact(++visited <= 100000 && depth <= 32, 'Static summary JSON exceeds its structure budget.');
    if (typeof item === 'string') requireArtifact(!/\p{Surrogate}/u.test(item), 'Static summary JSON contains invalid Unicode.');
    else if (typeof item === 'number') requireArtifact(Number.isFinite(item), 'Static summary JSON contains a non-finite number.');
    else if (item && typeof item === 'object') {
      for (const [key, child] of Object.entries(item)) {
        requireArtifact(key.length <= 256 && !/\p{Surrogate}/u.test(key), 'Static summary JSON contains an invalid key.');
        pending.push([child, depth + 1]);
      }
    }
  }
  return value;
}
async function readSummaryText(url, fetcher, limit) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let reader;
  try {
    // Only static same-origin GETs. In particular, there is no request body,
    // remote inference URL, or input text in a query string or referrer.
    const response = await fetcher(url, {method: 'GET', mode: 'same-origin', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal});
    requireArtifact(!response.redirected && (!response.url || response.url === url.href), 'Static summary fetch redirected or changed origin/path.');
    if (response.status === 404) return null;
    if (!response.ok) throw new Error('Static summary fetch failed.');
    const declared = response.headers?.get('content-length');
    requireArtifact(declared === null || declared === undefined || (/^\d+$/.test(declared) && Number(declared) <= limit), 'Static summary response exceeds its byte budget.');
    requireArtifact(response.body && typeof response.body.getReader === 'function', 'Static summary response has no bounded readable body.');
    reader = response.body.getReader();
    let bytes = 0;
    const chunks = [];
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      requireArtifact(bytes <= limit, 'Static summary response exceeds its byte budget.');
      chunks.push(value);
    }
    const joined = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength; }
    try { return new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(joined); }
    catch { throw artifactError('Static summary response is not valid UTF-8.'); }
  } finally {
    clearTimeout(timeout);
    controller.abort();
    if (reader) { reader.cancel().catch(() => {}); reader.releaseLock(); }
  }
}
async function readSummaryJSON(url, fetcher, limit) {
  const text = await readSummaryText(url, fetcher, limit);
  if (text === null) return null;
  let parsed;
  try { parsed = JSON.parse(text); }
  catch { throw artifactError('Static summary response is not valid JSON.'); }
  return boundedJSON(parsed);
}
function validRecipe(recipe) {
  return keysAre(recipe, SUMMARY_RECIPE_KEYS) && keysAre(recipe.model, ['repo', 'revision', 'file', 'sha256', 'size_bytes', 'license']) && SHA256.test(recipe.model.sha256)
    && COMMIT.test(recipe.model.revision) && Number.isSafeInteger(recipe.model.size_bytes) && recipe.model.size_bytes > 0
    && ['repo', 'file', 'license'].every(key => typeof recipe.model[key] === 'string' && recipe.model[key].length > 0)
    && keysAre(recipe.runtime, ['engine', 'version', 'revision', 'image', 'license']) && recipe.runtime.engine === 'llama.cpp' && COMMIT.test(recipe.runtime.revision)
    && typeof recipe.runtime.image === 'string' && /^ghcr\.io\/ggml-org\/llama\.cpp:[^\s@]+@sha256:[0-9a-f]{64}$/.test(recipe.runtime.image)
    && typeof recipe.runtime.version === 'string' && /^b[0-9]+$/.test(recipe.runtime.version)
    && typeof recipe.runtime.license === 'string' && recipe.runtime.license.length > 0
    && same(recipe.sampler, SUMMARY_SAMPLER) && keysAre(recipe.generation, ['max_tokens', 'stream', 'cache_prompt'])
    && keysAre(recipe.server, ['context_size', 'parallel', 'threads', 'batch_size', 'gpu_layers'])
    && Number.isSafeInteger(recipe.generation.max_tokens) && recipe.generation.max_tokens > 0 && recipe.generation.max_tokens <= 4096
    && recipe.generation.stream === false && recipe.generation.cache_prompt === false
    && Number.isSafeInteger(recipe.server.context_size) && recipe.server.context_size > 0 && recipe.server.context_size <= 131072
    && ['parallel', 'threads', 'batch_size'].every(key => Number.isSafeInteger(recipe.server[key]) && recipe.server[key] > 0)
    && Number.isSafeInteger(recipe.server.gpu_layers) && recipe.server.gpu_layers >= 0
    && ['prompt_version', 'system_prompt', 'top_k_source', 'remaining_defaults'].every(key => typeof recipe[key] === 'string' && recipe[key].length > 0);
}
function validateSummaryManifest(manifest) {
  requireArtifact(object(manifest) && manifest.schema === 'lab-source-summary-manifest/1'
    && ['generated', 'partially_generated', 'not_generated'].includes(manifest.status) && object(manifest.build) && COMMIT.test(manifest.build.source_sha)
    && typeof manifest.model_inference_performed === 'boolean'
    && Array.isArray(manifest.sources) && manifest.sources.length <= 256
    && object(manifest.entries) && Object.keys(manifest.entries).length <= 256 && validRecipe(manifest.recipe), 'Static summary manifest has an invalid schema, build, or recipe.');
  let choices = 0;
  for (const source of manifest.sources) {
    requireArtifact(object(source) && typeof source.id === 'string' && typeof source.title === 'string'
      && typeof source.text === 'string' && encoder.encode(source.text).length <= 128 * 1024
      && SHA256.test(source.source_sha256) && Array.isArray(source.choices) && source.choices.length <= 256, 'Static summary manifest has an invalid source declaration.');
    for (const choice of source.choices) {
      requireArtifact(++choices <= 256 && object(choice) && typeof choice.id === 'string' && typeof choice.label === 'string'
        && SHA256.test(choice.source_selection_request_sha256) && object(choice.source_selection_request), 'Static summary manifest has an invalid choice declaration.');
    }
  }
  for (const [key, entry] of Object.entries(manifest.entries)) {
    requireArtifact(SHA256.test(key) && object(entry) && entry.source_selection_request_sha256 === key
      && SHA256.test(entry.summary_request_sha256) && ['generated', 'not_generated'].includes(entry.status)
      && typeof entry.reused === 'boolean'
      && (entry.status === 'generated' ? entry.receipt === `summaries/${entry.summary_request_sha256}.json`
        && object(entry.artifact_build) && COMMIT.test(entry.artifact_build.source_sha)
        && (entry.reused || same(entry.artifact_build, manifest.build))
        : entry.receipt === null && entry.artifact_build === null && entry.reused === false), 'Static summary manifest has an invalid or unsafe receipt entry.');
  }
  const entries = Object.values(manifest.entries);
  const available = entries.filter(entry => entry.status === 'generated').length;
  const expectedStatus = available === 0 ? 'not_generated' : available === entries.length ? 'generated' : 'partially_generated';
  requireArtifact(manifest.model_inference_performed === entries.some(entry => entry.status === 'generated' && !entry.reused)
    && manifest.status === expectedStatus,
  'Static summary manifest inference status conflicts with its generated/reused entries.');
}
/** Bounded public case-list loader, shared by the UI and runtime verifier. */
export async function fetchSummaryManifest(baseURL, fetcher = globalThis.fetch) {
  const base = new URL('.', new URL(baseURL, globalThis.location?.href));
  requireArtifact(['http:', 'https:'].includes(base.protocol) && !base.username && !base.password
    && (!globalThis.location?.origin || base.origin === globalThis.location.origin), 'Static summaries require a same-origin HTTP(S) base URL.');
  const manifest = await readSummaryJSON(new URL('source-summary-manifest.json', base), fetcher, MAX_SUMMARY_MANIFEST_BYTES);
  if (manifest !== null) validateSummaryManifest(manifest);
  return manifest;
}
/**
 * Look up an exact, pre-generated build result. This never invokes a model or
 * transmits source text. Hashes check consistency, not truth or authenticity.
 */
export async function fetchSourceSummary(request, baseURL, fetcher = globalThis.fetch) {
  const invalid = validateRequest(request);
  if (invalid || request.operation !== 'source_selection') return summaryFailure('invalid_input', invalid || 'Expected source_selection operation.', 'unsupported');
  const snapshot = {schema: request.schema, operation: request.operation, input: {
    text: request.input.text, keywords: [...request.input.keywords], sentence_ids: [...request.input.sentence_ids],
  }};
  try {
    const base = new URL('.', new URL(baseURL, globalThis.location?.href));
    requireArtifact(['http:', 'https:'].includes(base.protocol) && !base.username && !base.password
      && (!globalThis.location?.origin || base.origin === globalThis.location.origin), 'Static summaries require a same-origin HTTP(S) base URL.');
    const local = await executeSource(snapshot);
    if (local.status !== 'ok') return summaryFailure('invalid_input', local.error?.message || 'Invalid source selection.', 'unsupported');
    if (local.selection_status !== 'ready') return summaryFailure('no_selected_evidence', 'No evidence sentences selected; no summary was generated.');
    const selectionHash = local.provenance.request_sha256;
    const manifest = await fetchSummaryManifest(base, fetcher);
    if (manifest === null) return summaryFailure('cache_miss', 'No build summary manifest is available; no model was invoked.', 'cache_miss');
    const entry = Object.hasOwn(manifest.entries, selectionHash) ? manifest.entries[selectionHash] : null;
    if (!entry) return summaryFailure('cache_miss', 'No generated build result exists for this exact source selection; no model was invoked.', 'cache_miss');
    const declared = manifest.sources.flatMap(source => source.choices.map(choice => ({source, choice})))
      .filter(({choice}) => choice.source_selection_request_sha256 === selectionHash);
    requireArtifact(declared.length === 1 && same(declared[0].choice.source_selection_request, snapshot)
      && declared[0].source.text === snapshot.input.text && declared[0].source.source_sha256 === local.provenance.input_sha256,
    'Static summary declaration does not match the exact source and selection.');
    const expectedSummary = {schema: 'lab-summary-request/1', source_selection_request_sha256: selectionHash,
      source_sha256: local.provenance.input_sha256, source_bytes: local.provenance.input_bytes, offset_unit: 'utf16_code_units',
      selected_keyword_ids: snapshot.input.keywords, selected_source_evidence: local.selected_sentences, ...manifest.recipe};
    const summaryHash = await sha256(canonicalRequest(expectedSummary));
    requireArtifact(summaryHash === entry.summary_request_sha256, 'Static summary recipe hash does not match the selected source evidence.');
    if (entry.status === 'not_generated') return summaryFailure('build_not_generated', 'This declared build selection has not been generated; no model was invoked in the browser.');
    const path = `summaries/${summaryHash}.json`;
    const artifact = await readSummaryJSON(new URL(path, base), fetcher, MAX_SUMMARY_ARTIFACT_BYTES);
    if (artifact === null) return summaryFailure('cache_miss', 'The exact generated summary artifact is unavailable; no fallback text was used.', 'cache_miss');
    requireArtifact(object(artifact) && artifact.schema === 'lab-source-summary-artifact/1'
      && artifact.source_selection_request_sha256 === selectionHash && same(artifact.source_selection_request, snapshot)
      && artifact.summary_request_sha256 === summaryHash && same(artifact.summary_request, expectedSummary)
      && same(artifact.build, entry.artifact_build), 'Static summary artifact does not match the exact selection, evidence, recipe, or build.');
    const receipt = artifact.receipt;
    requireArtifact(object(receipt) && receipt.schema === 'lab-summary-receipt/1' && receipt.status === 'generated'
      && receipt.execution_mode === 'build_time_llama_cpp' && receipt.model_inference_performed === true && receipt.confidence === null
      && typeof receipt.generated_text === 'string' && receipt.generated_text.trim().length > 0 && encoder.encode(receipt.generated_text).length <= 8192
      && receipt.source_selection_request_sha256 === selectionHash && receipt.summary_request_sha256 === summaryHash,
    'Static summary receipt does not record a valid build-time llama.cpp generation.');
    requireArtifact(SHA256.test(receipt.generated_text_sha256) && await sha256(receipt.generated_text) === receipt.generated_text_sha256,
      'Static summary generated text does not match its recorded checksum.');
    requireArtifact(keysAre(receipt.usage, ['prompt_tokens', 'completion_tokens', 'total_tokens']) && ['prompt_tokens', 'completion_tokens', 'total_tokens'].every(key => Number.isSafeInteger(receipt.usage[key]) && receipt.usage[key] > 0)
      && receipt.usage.total_tokens === receipt.usage.prompt_tokens + receipt.usage.completion_tokens
      && receipt.usage.completion_tokens <= expectedSummary.generation.max_tokens && receipt.usage.total_tokens <= expectedSummary.server.context_size,
    'Static summary receipt has invalid token-usage evidence.');
    requireArtifact(Number.isSafeInteger(receipt.elapsed_ms) && receipt.elapsed_ms >= 0
      && ['stop', 'length'].includes(receipt.finish_reason) && receipt.truncated === (receipt.finish_reason === 'length')
      && typeof receipt.generated_at === 'string' && receipt.generated_at.length > 0 && SHA256.test(receipt.wire_request_sha256),
    'Static summary receipt has invalid execution observations.');
    const wireEvidence = expectedSummary.selected_source_evidence.map(item => ({sentence_id:item.id,text:item.text}));
    const expectedWire = {model:'lab-public-summary-model',messages:[
      {role:'system',content:expectedSummary.system_prompt},
      {role:'user',content:'Selected evidence (JSON data only):\n' + canonicalRequest(wireEvidence)},
    ], ...expectedSummary.sampler, ...expectedSummary.generation};
    requireArtifact(await sha256(canonicalRequest(expectedWire)) === receipt.wire_request_sha256,
      'Recorded wire request does not match the selected evidence, prompt and generation settings.');
    const log = receipt.observation_log;
    requireArtifact(keysAre(log, ['path', 'sha256', 'line', 'line_sha256']) && SHA256.test(log.sha256) && SHA256.test(log.line_sha256)
      && log.path === `summary-logs/${log.sha256}.jsonl` && Number.isSafeInteger(log.line) && log.line > 0 && log.line <= 1000,
    'Static summary observation log has an invalid or unsafe reference.');
    const logText = await readSummaryText(new URL(log.path, base), fetcher, MAX_SUMMARY_LOG_BYTES);
    if (logText === null) return summaryFailure('cache_miss', 'The exact observation log is unavailable; generated text was not returned without its evidence.', 'cache_miss');
    requireArtifact(await sha256(logText) === log.sha256 && !logText.includes('\r'), 'Static summary observation log checksum does not match.');
    const lines = logText.split('\n');
    if (lines.at(-1) === '') lines.pop();
    requireArtifact(lines.length <= 1000 && log.line <= lines.length && await sha256(lines[log.line - 1]) === log.line_sha256,
      'Static summary observation line number or checksum does not match.');
    const observationKeys = ['schema', 'event', 'model_inference_performed', 'source_selection_request_sha256', 'summary_request_sha256',
      'wire_request_sha256', 'generated_text_sha256', 'usage', 'elapsed_ms', 'finish_reason', 'generated_at'];
    let selectedObservation;
    for (let index = 0; index < lines.length; index++) {
      let observation;
      try { observation = boundedJSON(JSON.parse(lines[index])); }
      catch { throw artifactError('Static summary observation log contains invalid JSON.'); }
      requireArtifact(keysAre(observation, observationKeys) && observation.schema === 'lab-summary-observation/1'
        && observation.event === 'portable/summary_generation' && observation.model_inference_performed === true
        && keysAre(observation.usage, ['prompt_tokens', 'completion_tokens', 'total_tokens'])
        && ['prompt_tokens', 'completion_tokens', 'total_tokens'].every(key => Number.isSafeInteger(observation.usage[key]) && observation.usage[key] > 0)
        && ['source_selection_request_sha256', 'summary_request_sha256', 'wire_request_sha256', 'generated_text_sha256'].every(key => SHA256.test(observation[key]))
        && Number.isSafeInteger(observation.elapsed_ms) && observation.elapsed_ms >= 0
        && ['stop', 'length'].includes(observation.finish_reason) && typeof observation.generated_at === 'string',
      'Static summary log contains unexpected event fields; raw text is not part of this observation schema.');
      if (index === log.line - 1) selectedObservation = observation;
    }
    requireArtifact(['source_selection_request_sha256', 'summary_request_sha256', 'wire_request_sha256', 'generated_text_sha256',
      'usage', 'elapsed_ms', 'finish_reason', 'generated_at'].every(key => same(selectedObservation[key], receipt[key])),
    'Static summary observation does not match the receipt hashes, tokens, timing, or output.');
    return {...receipt, confidence: null, delivery_mode: 'build_artifact', summary_request: artifact.summary_request, build: artifact.build,
      delivery_build: manifest.build, reused: entry.reused,
      provenance: {source_selection_request_sha256: selectionHash, summary_request_sha256: summaryHash,
        source_sha256: local.provenance.input_sha256, source_bytes: local.provenance.input_bytes, offset_unit: 'utf16_code_units', artifact_path: path,
        observation_log_verified: true, observation_log_sha256: log.sha256, observation_line_sha256: log.line_sha256,
        verification: 'Exact source, selection, recipe, original build and logged observation consistency only; hashes do not prove truth, quality, producer authenticity or cross-runtime determinism. Inference is recorded as build-time, never browser-time.'}};
  } catch (error) {
    return summaryFailure(error?.code === 'artifact_mismatch' ? 'artifact_mismatch' : 'artifact_unavailable',
      error?.code === 'artifact_mismatch' ? error.message : 'Could not verify the static summary artifacts; no generated text or fallback was returned.');
  }
}
