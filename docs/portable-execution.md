# Portable execution, built once

The public `/execution/` page is a consumer of the reusable execution contract,
not a second Python implementation or a hosted backend. The root still redirects
to the existing portfolio. GitHub Pages serves static files only:
https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages

## One request, two explicit capabilities

`lab-execution/1` accepts exactly `schema`, `operation`, and `input`:

```json
{"schema":"lab-execution/1","operation":"trace_summary","input":{"jsonl":"{\"event\":\"tools/call\",\"outcome\":\"success\"}\n"}}
```

- `trace_summary`: browser and Node import the identical JavaScript module.
  Python invokes that same fixed Node command. Counts come from recorded
  `tools/call` outcomes (`success`, `empty`, `error`, unknown), not caller-side
  Ironmate events, fabricated model answers, or a second heuristic implementation.
- `adapter_lookup`: Python calls existing `load_adapter_cache` and
  `execute_via_adapter`; Node invokes that fixed Python command. Input is
  `{"selector":{"query":"public-example"}}` or a `position` selector. The
  adapter path is a trusted CLI option, never a request-selected module/path.
  Existing contract 2.0 receipts and whole-cache verification are retained.
  There is no implicit stored-default approximation.
- The browser cannot invoke Python. It fetches a receipt only for an exact
  request built into the artifact, labels it `delivery_mode=build_artifact`,
  and returns `cache_miss` for any other valid input. No invented evaluation,
  confidence, fallback answer, or successful execution for an unseen request.

Both operations return `model_inference_performed=false`. Top-level confidence
is `null`: this tool has no ground-truth calibration. Jev probability fields
already present in logs are extracted separately with their line and original
`prob_source`; they are unverified recorded claims, not confidence in the
analysis. `noul` is a recorded proposition probability, not a generic confidence.
No API keys, paid model, browser Python runtime or external host is needed.

## Reuse without Pages

Requires Python 3.12+ and Node 22+. No Python/npm packages are installed by this
slice. From the repository root (or the extracted `runtime.zip` root):

```sh
PYTHONPATH=src python -S -m mcp_toolcall_lab.portable_execution < request.json
node src/mcp_toolcall_lab/static/portable_cli.mjs < request.json
# The adapter is a caller-selected, locally trusted artifact, not user JSON code.
PYTHONPATH=src python -S -m mcp_toolcall_lab.portable_execution --adapter adapter.json < adapter-request.json
node src/mcp_toolcall_lab/static/portable_cli.mjs --adapter adapter.json < adapter-request.json
```

`NODE` and `PYTHON` select locally trusted executable names/paths for the bridges;
requests cannot change them. Cross-runtime leaf calls use `--native-only` so they
cannot recurse. The direct module APIs are `execute(request, adapter_path=...)`
and `execute(request, {adapter})`; the browser core exports `executeTrace` and
`fetchBuildReceipt`. MCP-assisted callers can prepare/validate the same envelope
and call the host API. This change does not register a new MCP tool or claim
that an MCP transport was used.

Reuse audit: the existing engine adapter and shared JSONL writer remain owners
of their contracts. Aoi's `fetch_module.py` downloads/verifies pure Python
wheels; it is not an executor and is unnecessary for this stdlib-only path.
Existing source dispatch already has `source_access.Registry`; this two-operation
host introduces no competing registry, arbitrary import, package resolver,
model downloader, bundler, or remote execution framework. The bridge follows the
fixed-command JSON pattern of existing Ironmate wrappers. Browser/Node share
one source file, so there is no mirrored log-analysis algorithm to drift.

## Input and evidence boundaries

- JSON envelope: at most 2 MiB on CLI; trace text at most 1 MiB UTF-8 and 10,000
  lines; selectors at most 4096 UTF-8 bytes. Invalid Unicode is rejected.
- Any malformed/non-object JSONL row rejects the entire analysis. No partial
  success is silently returned. Unknown outcomes are counted as unknown.
- Bridge deadline: 15 seconds. Node limits Python stdout/stderr to 4 MiB.
  The Python bridge runs only the fixed, input-bounded analyzer and rejects
  receipts over 4 MiB; these are not OS-level isolation/resource guarantees.
- Log SHA-256, request SHA-256, input bytes and line annotations identify the
  supplied evidence. They do not authenticate the producer or verify log truth.
- When `MCP_TOOLCALL_LOG` is set, the Python CLI appends `portable/execution`
  through the existing writer without copying raw input. It does not emit a
  false `tools/call` event. Node direct analysis has no implicit file writes.
- Trace analysis stays in the browser. Adapter lookup sends the request SHA-256
  in a same-origin static URL, not raw input. Low-entropy selectors may be
  guessable from that hash. Only static examples/receipts are fetched. Rendering uses textContent, never untrusted innerHTML.
- The build publishes only a generated public example: actual deterministic
  mock catalog function calls recorded through `record_call`, not live MCP,
  network data, private logs or model inference. CLI users own access controls
  when choosing their own caches/logs; this is not a sandbox.

## Docker export and publication

```sh
docker build --file docker/portable-pages/Dockerfile --target artifact \
  --build-arg SOURCE_SHA="$(git rev-parse HEAD)" --output type=local,dest=_portable_site .
# Equivalent source-level export when Docker is not available:
PYTHONPATH=src python -S scripts/build_portable_pages.py \
  --source-sha "$(git rev-parse HEAD)" --out _portable_site
```

Docker's final scratch target contains static files only. No `docker run`,
Compose services, port, daemon, browser inference or model downloads are needed.
Base images use explicit runtime/OS tags; those tags are not immutable digest
pins. `build.json` records the declared source commit, runtime versions and
asset hashes. In CI the declared SHA is the checked-out commit; local callers
must supply the corresponding commit and account for uncommitted edits.
Use a fresh output directory: generation rejects an existing execution/ tree
to prevent stale receipts from leaking across builds. Generated receipts are
snapshots, not latest-live values. Runtime source and
existing vendored dependency licenses and lock are shipped alongside the UI.

`portable-pages.yml` is the sole Pages publisher. It builds on PRs, uploads the
static build for inspection, and deploys only main. The old `stub-pages.yml`
continues its separate Docker/MCP smoke/report artifact job, but no longer
publishes a competing root redirect. No Pages settings or credentials change.

Validation: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_portable_execution.py'`.
Docker export itself is a separate required CI job; source-level generation is
not evidence that Docker ran.
