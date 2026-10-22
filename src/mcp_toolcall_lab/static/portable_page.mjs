import {executeTrace, fetchBuildReceipt, validateRequest, failure} from './runtime/src/mcp_toolcall_lab/static/portable_execution.mjs';
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
  const result = value.operation === 'trace_summary' ? await executeTrace(value) : await fetchBuildReceipt(value, new URL('./', import.meta.url));
  if (current === generation) show(result);
});
request.addEventListener('input', () => { generation++; output.textContent = 'Input changed. Run again for a matching receipt.'; status.textContent = 'Not executed for this input.'; });
document.querySelector('#sample').addEventListener('click', () => sample('example-request.json'));
document.querySelector('#adapter').addEventListener('click', () => sample('adapter-request.json'));
document.querySelector('#clear').addEventListener('click', () => { generation++; request.value = ''; output.textContent = 'No execution yet.'; status.textContent = 'Cleared.'; });
await sample('example-request.json');
