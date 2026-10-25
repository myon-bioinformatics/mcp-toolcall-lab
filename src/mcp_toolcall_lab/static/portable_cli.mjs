#!/usr/bin/env node
/** Node host for the shared browser core; Python owns adapter execution. */
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {webcrypto} from 'node:crypto';
import {realpath, open} from 'node:fs/promises';
import {CONTRACT, executeTrace, executeSource, fetchSourceSummary, failure, validateRequest} from './portable_execution.mjs';
globalThis.crypto ??= webcrypto;
const MAX_REQUEST = 2 * 1024 * 1024;
async function localSummary(request, directory) {
  // Reuse the browser verifier without a server or network. URLs are merely
  // identifiers for this strictly bounded filesystem fetch implementation.
  const base = new URL('https://local-artifacts.invalid/execution/');
  let root;
  try { root = await realpath(directory); }
  catch { return failure('artifact_unavailable', 'Static summary directory is unavailable.'); }
  return fetchSourceSummary(request, base, async url => {
    const relative = url.href.slice(base.href.length);
    if (!url.href.startsWith(base.href) || !/^(source-summary-manifest\.json|summaries\/[a-f0-9]{64}\.json|summary-logs\/[a-f0-9]{64}\.jsonl)$/.test(relative)) throw new Error('Unexpected static artifact');
    const filename = path.join(root, relative);
    let handle;
    try {
      if (await realpath(filename) !== filename) throw new Error('Symlinked artifacts are not accepted');
      handle = await open(filename, 'r');
      const stat = await handle.stat();
      if (!stat.isFile() || stat.size > MAX_REQUEST) throw new Error('Artifact exceeds byte budget');
      const buffer = Buffer.alloc(MAX_REQUEST + 1);
      let size = 0;
      while (size < buffer.length) {
        const {bytesRead} = await handle.read(buffer, size, buffer.length - size, null);
        if (!bytesRead) break;
        size += bytesRead;
      }
      if (size > MAX_REQUEST) throw new Error('Artifact exceeds byte budget');
      return new Response(buffer.subarray(0, size), {status:200,headers:{'content-length':String(size)}});
    } catch (error) {
      if (error.code === 'ENOENT') return new Response(null, {status:404});
      throw error;
    } finally { if (handle) await handle.close(); }
  });
}
export async function execute(request, {adapter, summaryDir, nativeOnly = false} = {}) {
  const invalid = validateRequest(request);
  if (invalid) return failure('invalid_input', invalid);
  if (summaryDir) {
    if (request.operation !== 'source_selection' || adapter) return failure('invalid_input', '--summary-dir requires only source_selection, without --adapter.');
    return localSummary(request, summaryDir);
  }
  if (request.operation === 'trace_summary') return executeTrace(request);
  if (request.operation === 'source_selection') return executeSource(request);
  if (nativeOnly) return failure('runtime_unavailable', 'Node native-only mode cannot execute Python adapters.');
  if (!adapter) return failure('adapter_required', 'Select a verified local cache with --adapter.');
  const sourceRoot = fileURLToPath(new URL('../../', import.meta.url));
  const child = spawnSync(process.env.PYTHON || 'python3', ['-S', '-m', 'mcp_toolcall_lab.portable_execution', '--native-only', '--adapter', adapter], {
    input: JSON.stringify(request), encoding: 'utf8', timeout: 15000, maxBuffer: 4 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)},
  });
  if (child.error || child.status !== 0) return failure('bridge_failed', 'Python adapter process failed or exceeded its 15-second/4-MiB bridge limit.');
  try {
    const receipt = JSON.parse(child.stdout);
    return receipt.schema === CONTRACT ? receipt : failure('bridge_failed', 'Invalid Python receipt.');
  } catch { return failure('bridge_failed', 'Python did not return one JSON receipt.'); }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let text = '', size = 0;
  const chunks = [];
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_REQUEST) { console.log(JSON.stringify(failure('invalid_input', 'Request exceeds 2 MiB.'))); process.exit(0); }
    chunks.push(chunk);
  }
  let result;
  try {
    text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(Buffer.concat(chunks));
    const args = process.argv.slice(2), options = {};
    for (let index = 0; index < args.length; index++) {
      const name = {'--adapter':'adapter','--summary-dir':'summaryDir','--native-only':'nativeOnly'}[args[index]];
      if (!name || name in options) throw new Error('Unknown or duplicate option');
      if (name === 'nativeOnly') options[name] = true;
      else {
        if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error('Missing option value');
        options[name] = args[++index];
      }
    }
    result = await execute(JSON.parse(text), options);
  } catch { result = failure('invalid_input', 'Expected one JSON request and supported CLI options.'); }
  process.stdout.write(JSON.stringify(result) + '\n');
}
