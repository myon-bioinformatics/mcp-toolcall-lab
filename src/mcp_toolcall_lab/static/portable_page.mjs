import {executeTrace, executeSource, fetchBuildReceipt, fetchSourceSummary, fetchSummaryManifest, canonicalRequest, sha256, validateRequest, failure} from './runtime/src/mcp_toolcall_lab/static/portable_execution.mjs';
const request = document.querySelector('#request'), output = document.querySelector('#result'), status = document.querySelector('#status');
let generation = 0;
function show(result) { output.textContent = JSON.stringify(result, null, 2); status.textContent = `${result.status} · ${result.delivery_mode || result.execution_mode}`; }
async function sample(name) {
  const current = ++generation;
  output.textContent = 'No execution yet.'; status.textContent = 'Loading public example…';
  try {
    const response = await fetch(new URL(name, import.meta.url));
    if (!response.ok) throw new Error('Example unavailable');
    const value = await response.json();
    if (current !== generation) return;
    request.value = JSON.stringify(value, null, 2); status.textContent = 'Input ready. Review it, then run.';
  } catch { if (current === generation) status.textContent = 'Example unavailable. You can still enter your own trace_summary request.'; }
}
document.querySelector('#run').addEventListener('click', async () => {
  const current = ++generation;
  output.textContent = 'No result yet.'; status.textContent = 'Validating input…';
  if (new TextEncoder().encode(request.value).length > 2 * 1024 * 1024) { show(failure('invalid_input', 'Request exceeds 2 MiB.')); return; }
  let value;
  try { value = JSON.parse(request.value); } catch { show(failure('invalid_input', 'Request is not JSON.')); return; }
  const invalid = validateRequest(value);
  if (invalid) { show(failure('invalid_input', invalid)); return; }
  const result = value.operation === 'trace_summary' ? await executeTrace(value) : value.operation === 'source_selection' ? await executeSource(value) : await fetchBuildReceipt(value, new URL('./', import.meta.url));
  if (current === generation) show(result);
});
request.addEventListener('input', () => { generation++; output.textContent = 'Input changed. Run again for a matching receipt.'; status.textContent = 'Not executed for this input.'; });
document.querySelector('#sample').addEventListener('click', () => sample('example-request.json'));
document.querySelector('#adapter').addEventListener('click', () => sample('adapter-request.json'));
document.querySelector('#clear').addEventListener('click', () => { generation++; request.value = ''; output.textContent = 'No execution yet.'; status.textContent = 'Cleared.'; });
const sourceText = document.querySelector('#source-text');
const sourceStatus = document.querySelector('#source-status');
const keywordOptions = document.querySelector('#keyword-options');
const sentenceOptions = document.querySelector('#sentence-options');
const sourceNotes = document.querySelector('#source-notes');
const summaryStatus = document.querySelector('#summary-status');
const evidence = document.querySelector('#selected-evidence');
const download = document.querySelector('#summary-download');
const lookup = document.querySelector('#summary-lookup');
const caseDownload = document.querySelector('#source-case-download');
const generatedSummary = document.querySelector('#generated-summary');
const summaryNotes = document.querySelector('#summary-notes');
const caseOptions = document.querySelector('#source-cases');
const caseStatus = document.querySelector('#source-cases-status');
let sourceGeneration = 0, summaryGeneration = 0, caseGeneration = 0, sourceReceipt = null, sourceRequest = null, publicCases = [];
function clearPrepared() {
  sourceReceipt = null; sourceRequest = null; summaryGeneration++;
  sourceNotes.textContent = 'No execution for this source/selection.';
  evidence.textContent = '';
  summaryStatus.textContent = 'No sentences selected. An LLM summary has not been generated.';
  download.disabled = true;
  lookup.disabled = true; caseDownload.disabled = true; generatedSummary.textContent = ''; summaryNotes.textContent = 'No summary lookup for this selection.';
}
function invalidateSource(message) {
  sourceGeneration++;
  clearPrepared();
  keywordOptions.replaceChildren(); keywordOptions.disabled = true;
  sentenceOptions.replaceChildren(); sourceStatus.textContent = message;
}
sourceText.addEventListener('input', () => invalidateSource('Source changed. Extract hints again; previous selections and results were discarded.'));
document.querySelector('#source-clear').addEventListener('click', () => {
  sourceText.value = ''; invalidateSource('Source cleared.');
});
async function selectSource({fresh = false, resetSentences = false, preset = null} = {}) {
  const current = ++sourceGeneration;
  const keywords = preset ? [...preset.keywords] : fresh ? [] : [...keywordOptions.selectedOptions].map(option => option.value);
  const sentenceIds = preset ? [...preset.sentence_ids] : fresh || resetSentences ? [] : [...sentenceOptions.querySelectorAll('input:checked')].map(input => input.value);
  const value = {schema:'lab-execution/1', operation:'source_selection', input:{text:sourceText.value,keywords,sentence_ids:sentenceIds}};
  clearPrepared(); sourceStatus.textContent = 'Inspecting source…';
  keywordOptions.disabled = true;
  sentenceOptions.querySelectorAll('input').forEach(input => { input.disabled = true; });
  let result;
  try { result = await executeSource(value); }
  catch { result = failure('runtime_unavailable', 'Local source analysis is unavailable in this browser. No summary was generated.'); }
  if (current !== sourceGeneration) return;
  sourceNotes.textContent = JSON.stringify(result, null, 2);
  if (result.status !== 'ok') {
    keywordOptions.replaceChildren(); sentenceOptions.replaceChildren();
    sourceStatus.textContent = result.error?.message || 'Source selection unavailable.';
    return;
  }
  sourceReceipt = result; sourceRequest = value;
  keywordOptions.replaceChildren();
  for (const option of result.keyword_options) {
    const element = document.createElement('option');
    element.value = option.id; element.textContent = `${option.text} (${option.count})`;
    element.selected = keywords.includes(option.id); keywordOptions.append(element);
  }
  keywordOptions.disabled = !result.keyword_options.length;
  sentenceOptions.replaceChildren();
  for (const sentence of result.candidates) {
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = sentence.id;
    input.checked = sentenceIds.includes(sentence.id);
    input.addEventListener('change', () => selectSource());
    const text = document.createElement('span'); text.textContent = sentence.text;
    label.append(input, text); sentenceOptions.append(label);
  }
  sourceStatus.textContent = result.selection_status === 'empty_source' ? 'No source text. Nothing was selected or summarized.' : result.selection_status === 'no_candidates' ? 'No matching source sentences. No summary request was prepared.' : `${result.candidates.length} candidate sentences · ${result.selected_sentences.length} selected. Hints are lexical heuristics, not confidence.`;
  if (result.keyword_options_truncated) sourceStatus.textContent += ` Showing ${result.keyword_options.length} of ${result.metrics.keyword_options_total} lexical hints.`;
  evidence.textContent = result.selected_sentences.map(sentence => sentence.text).join('\n\n');
  summaryStatus.textContent = result.summary_request ? 'Evidence selected. No summary loaded. Check for a build-time result matching this exact source and selection.' : 'No sentences selected. An LLM summary has not been generated.';
  download.disabled = !result.summary_request;
  lookup.disabled = !result.summary_request;
  caseDownload.disabled = !result.summary_request;
}
document.querySelector('#source-extract').addEventListener('click', () => selectSource({fresh:true}));
keywordOptions.addEventListener('change', () => selectSource({resetSentences:true}));
lookup.addEventListener('click', async () => {
  if (!sourceReceipt?.summary_request || !sourceRequest) return;
  const current = ++summaryGeneration, sourceVersion = sourceGeneration;
  const value = structuredClone(sourceRequest);
  lookup.disabled = true; generatedSummary.textContent = '';
  summaryNotes.textContent = 'Reading static build artifacts…'; summaryStatus.textContent = 'Looking for this exact declared selection…';
  let result;
  try { result = await fetchSourceSummary(value, new URL('./', import.meta.url)); }
  catch { result = {status:'unsupported',generated_text:null,confidence:null,model_inference_performed:false,error:{code:'artifact_unavailable',message:'Static summary lookup unavailable.'}}; }
  if (current !== summaryGeneration || sourceVersion !== sourceGeneration) return;
  summaryNotes.textContent = JSON.stringify(result, null, 2);
  lookup.disabled = false;
  if (result.status === 'generated' && result.model_inference_performed === true && typeof result.generated_text === 'string') {
    generatedSummary.textContent = result.generated_text;
    const completion = result.truncated ? 'Partial output: the recorded build stopped at the token limit. ' : 'Generated during the recorded build. ';
    summaryStatus.textContent = completion + 'Not generated in this browser. Review against the selected source; confidence is unknown and quality is not guaranteed.';
  } else {
    summaryStatus.textContent = result.status === 'not_generated' || result.status === 'cache_miss' ? 'Not generated for this exact source and selection. No model was run and no summary is substituted.' : 'Summary unavailable: ' + (result.error?.message || result.status);
  }
});
document.querySelector('#source-examples').addEventListener('click', async () => {
  const current = ++caseGeneration;
  caseOptions.disabled = true; caseStatus.textContent = 'Loading static public cases…';
  try {
    const manifest = await fetchSummaryManifest(new URL('./', import.meta.url));
    if (!manifest) throw new Error('manifest unavailable');
    if (manifest.schema !== 'lab-source-summary-manifest/1' || !Array.isArray(manifest.sources) || manifest.sources.length > 256) throw new Error('invalid manifest');
    const cases = [];
    for (const source of manifest.sources) {
      if (typeof source.title !== 'string' || source.title.length > 160 || typeof source.text !== 'string' || source.text.length > 4096 || !Array.isArray(source.choices)) throw new Error('invalid public source');
      for (const choice of source.choices) {
        const value = choice.source_selection_request;
        if (cases.length >= 256 || typeof choice.label !== 'string' || choice.label.length > 160 || validateRequest(value) || value.operation !== 'source_selection' || value.input.text !== source.text || await sha256(canonicalRequest(value)) !== choice.source_selection_request_sha256) throw new Error('invalid public case');
        cases.push({label:`${source.title} · ${choice.label}`,request:value});
      }
    }
    if (current !== caseGeneration) return;
    publicCases = cases; caseOptions.replaceChildren(new Option('Choose a public case', ''));
    cases.forEach((item, index) => caseOptions.append(new Option(item.label, String(index))));
    caseOptions.disabled = !cases.length;
    caseStatus.textContent = `${cases.length} declared cases. Build status: ${manifest.status === 'generated' ? 'generated' : manifest.status === 'partially_generated' ? 'some cached cases available' : 'not generated'}.`;
    if (manifest.corpus_window?.has_more) caseStatus.textContent += ' More cases remain in the local corpus for a later build window.';
  } catch {
    if (current !== caseGeneration) return;
    publicCases = []; caseOptions.replaceChildren(new Option('No public cases available', '')); caseStatus.textContent = 'Public build cases unavailable. Your pasted source still works locally.';
  }
});
caseOptions.addEventListener('change', async () => {
  if (caseOptions.value === '') return;
  const item = publicCases[Number(caseOptions.value)];
  if (!item) return;
  sourceText.value = item.request.input.text;
  invalidateSource('Public source loaded.');
  await selectSource({preset:item.request.input});
});
download.addEventListener('click', () => {
  if (!sourceReceipt?.summary_request) return;
  const file = new Blob([JSON.stringify(sourceReceipt.summary_request, null, 2) + '\n'], {type:'application/json'});
  const url = URL.createObjectURL(file);
  const anchor = document.createElement('a'); anchor.href = url;
  anchor.download = `summary-request-${sourceReceipt.provenance.input_sha256.slice(0,12)}.json`;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
caseDownload.addEventListener('click', () => {
  if (!sourceRequest || !sourceReceipt?.summary_request) return;
  const file = new Blob([JSON.stringify(sourceRequest, null, 2) + '\n'], {type:'application/json'});
  const url = URL.createObjectURL(file);
  const anchor = document.createElement('a'); anchor.href = url;
  anchor.download = `source-selection-${sourceReceipt.provenance.request_sha256.slice(0,12)}.json`;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
await sample('example-request.json');
