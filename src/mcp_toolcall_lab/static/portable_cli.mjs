#!/usr/bin/env node
/** Node host for the shared browser core; Python owns adapter execution. */
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {webcrypto} from 'node:crypto';
import {CONTRACT, executeTrace, failure, validateRequest} from './portable_execution.mjs';
globalThis.crypto ??= webcrypto;
const MAX_REQUEST = 2 * 1024 * 1024;
export async function execute(request, {adapter, nativeOnly = false} = {}) {
  const invalid = validateRequest(request);
  if (invalid) return failure('invalid_input', invalid);
  if (request.operation === 'trace_summary') return executeTrace(request);
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
    const args = process.argv.slice(2), index = args.indexOf('--adapter');
    if (args.some((arg, n) => arg !== '--native-only' && arg !== '--adapter' && !(index >= 0 && n === index + 1))) throw new Error('Unknown option');
    result = await execute(JSON.parse(text), {nativeOnly: args.includes('--native-only'), adapter: index >= 0 ? args[index + 1] : undefined});
  } catch { result = failure('invalid_input', 'Expected one JSON request and supported CLI options.'); }
  process.stdout.write(JSON.stringify(result) + '\n');
}
