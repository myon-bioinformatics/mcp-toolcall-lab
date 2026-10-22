/** Portable deterministic execution core. No DOM, Node APIs, network or model. */
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
  const sort = value => object(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sort(value[key])])) : value;
  return JSON.stringify(sort(request));
}
export async function sha256(text) {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', encoder.encode(text));
  return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
}
export function failure(code, message, status = 'unsupported') {
  return {schema: CONTRACT, status, execution_mode: 'unsupported', model_inference_performed: false, metrics: null, confidence: null, error: {code, message}};
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
