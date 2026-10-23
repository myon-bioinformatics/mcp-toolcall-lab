# Build-time public-source summaries

Source selection and generation are different operations. Browser/Node/Python
selection is deterministic lexical processing, with exact UTF-16 evidence spans.
The build generator invokes a real, pinned llama.cpp process for declared public
selections. A static Pages visit only retrieves an already-generated artifact;
it never runs a model, calls a hosted inference API, or uploads pasted source text.

## Pinned local runtime

This path reuses the repository's existing tiny GGUF rather than adding a new
model downloader or an API provider:

- `bartowski/SmolLM2-135M-Instruct-GGUF`, revision
  `09816acd5d99df7be770d85ea30822623dab342c`, file
  `SmolLM2-135M-Instruct-Q4_K_M.gguf`.
- Exact size: **105,454,432 bytes**, approximately 101 MiB. SHA-256:
  `2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d`.
- The [quantizer's model card](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF)
  and the [original model card](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct)
  list Apache-2.0. The [GGUF file page](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/blob/main/SmolLM2-135M-Instruct-Q4_K_M.gguf)
  independently exposes the same SHA-256. The repository records the exact
  commit-pinned download URL and earlier verification run in
  `docker/stub-pages/models/model.gguf.provenance.json`.
- llama.cpp `b11046`, revision `60081bb2b5b3294165a4d67c5cbeebe74c868014`,
  uses the existing official server image digest
  `sha256:9002e60256635720c05c50455fdebf992a98dc25466dc932758ecd7936244c1c`.
  Its [license is MIT](https://github.com/ggml-org/llama.cpp/blob/60081bb2b5b3294165a4d67c5cbeebe74c868014/LICENSE).

The generator does not download anything. When preparing a build, the existing
`scripts/fetch_tiny_cpu_gguf.py` fetches and checks the model against its pin.
The generator checks the size and hash again, inspects the selected executable's
version/revision, records its binary SHA-256, and starts its own CPU process on
`127.0.0.1`. It checks `/props` against the launched model path and `top_k` default.
It strips `LLAMA_ARG_*` environment overrides and always terminates the process.
The version string is not cryptographic producer authentication; use the pinned
official image for the build and preserve its provenance.

No model weights, executable, model server logs, API credentials or running
service belong in the published static artifact. The generation stage needs
sufficient local disk/RAM and network access to prepare its pinned dependencies;
the completed Pages output needs neither a GPU nor a backend.

## Fixed parameters, not guaranteed deterministic text

The resolved request explicitly records:

- temperature `0`, seed `42`, top-p `0.75`, repeat penalty `1`;
- integer top-k `40`, the default verified in the
  [pinned engine source](https://github.com/ggml-org/llama.cpp/blob/60081bb2b5b3294165a4d67c5cbeebe74c868014/common/common.h);
- `max_tokens=128`, `stream=false`, `cache_prompt=false`;
- CPU-only launch, one thread, one slot, context size 2048, batch size 64;
- exact model/runtime identity, system prompt and prompt version.

`top_k=0.75` is invalid: top-k counts candidate tokens, unlike top-p's fraction.
Temperature zero requests greedy sampling. Fixed settings do **not** promise
identical model output across hardware, runtime builds or floating-point paths.
The [pinned server documentation](https://github.com/ggml-org/llama.cpp/blob/60081bb2b5b3294165a4d67c5cbeebe74c868014/tools/server/README.md)
also documents cache-related nondeterminism. This example records one execution;
it is not a quality benchmark or a calibrated confidence estimate. In particular,
the tiny English model is an inexpensive integration demonstration, not a
production multilingual summarizer. Truncation is retained as `finish_reason`
and `truncated`, never hidden by inventing a completed sentence.

## Public evidence boundary

The default seed `fixtures/portable_summary/public_sources.json` contains 12
canonical, distinct keyword/sentence cases across four synthetic subject sources:
research archive (4), seedling light trial (3), gear-housing tests (3), and audio
duration study (2). The original archive text and its four choice hashes remain
unchanged. Sources are 239–284 UTF-8 bytes, with no user data or personal geography.
Five cases select no keyword filter, five select one keyword, and two select two
keywords. Two cases select one sentence, nine select two to four sentences, and one
selects five. Reordered arrays or renamed caller IDs do not count as new cases.
These are input/selection coverage counts, not model-quality measurements.
The fixture is checksum-pinned for privacy review. It is a seed, not the permanent corpus.
The build budget is at most 16 **uncached generations**,
4 KiB per public source, and eight selected sentences / 2 KiB evidence per
summary. No automatic exhaustive combination generation is performed.

Only selected evidence enters the local prompt. It is serialized as JSON under
a system instruction to treat it as untrusted data, not instructions. The model
has no tools. This is an input boundary, not a proof that a small model will never
hallucinate or follow a prompt injection. No generated claim is treated as
verified truth, and confidence stays `null`.

Two keys keep lookup and generation identity separate:

1. `source_selection_request_sha256` binds the exact source text and selected
   keyword/sentence IDs, including order. This is the browser lookup key.
2. `summary_request_sha256` also binds source hash/bytes, selected evidence with
   original UTF-16 offsets, keyword IDs, model/runtime, fixed sampler, server
   settings and exact prompt. This names `summaries/<hash>.json`.

An edited source, a changed selection, a new sampler/model/prompt or an unknown
post-build input cannot borrow another result. No artifact means
`not_generated` / `cache_miss`, not a fabricated summary. Content hashes establish
consistency only, not producer identity or the factual correctness of text.

## Build API and honest no-generation export

The standalone module needs only stdlib Python and the existing Node selection
bridge. From a source checkout:

```sh
# Preview the declared public cases without invoking or downloading a model.
PYTHONPATH=src python -S -m mcp_toolcall_lab.portable_summary_build \
  --out /tmp/summary-preview --source-sha "$(git rev-parse HEAD)"

# Generate with already-prepared pinned local dependencies.
PYTHONPATH=src python -S -m mcp_toolcall_lab.portable_summary_build \
  --out /tmp/summary-generated --source-sha "$(git rev-parse HEAD)" \
  --model docker/stub-pages/models/model.gguf --llama-server /app/llama-server
```

`build_summaries(output_dir, *, source_sha, model_path=None, llama_server=None)`
returns and writes `source-summary-manifest.json`. Omitting both runtime paths
emits declared `not_generated` misses plus any explicitly supplied, verified
cached results. It does not invoke a model or fabricate a generation log.
Supplying only one path is an error. Model/runtime mismatch, malformed responses,
HTTP redirects, tool calls or missing actual token usage stop the build. The HTTP
client permits only the owned loopback endpoint and disables environment proxies.
A failed or partially successful batch does not publish partial receipts.

`prepare_summary_request(selection_request)` and `summary_request_sha256(request)`
are preparation/identity APIs, not an authorization to generate arbitrary inputs.
The standalone generator refuses an existing summary output rather than silently
mixing it with stale receipts.

## Incremental JSONL corpus and exact-result reuse

An explicitly supplied `--reviewed-corpus PATH` replaces the demonstration seed
with a window from a local corpus directory or strict JSONL. It does not silently
approve newly pasted browser text for publication. Every exported case needs its
own `reviewed_public` declaration. Use `portable_summary_corpus` to ingest validated
source-selection requests, keep their provenance, and archive/retrieve JSONL
shards; see [the corpus documentation](summary-corpus.md) for review and archive controls.

`--result-cache DIR` is an explicit durable local cache of actual complete
execution receipts and their original observation logs. The full summary recipe
hash, source selection, generated-text hash, usage and observation-log bytes are
checked before reuse. A changed source, selection, prompt, model or sampler is a
new key, not a reason to overwrite an old result. Old results retain their original
build and execution timestamp. Cache hits do not launch llama.cpp, emit fresh
generation events, or claim the model ran again.
The reviewed corpus, durable cache, model and executable must remain outside the public output
directory, including resolved symlink targets. The exporter rejects nested paths
before writing so private corpus/cache contents cannot become accidental assets.

```sh
PYTHONPATH=src python -S -m mcp_toolcall_lab.portable_summary_build \
  --out /tmp/summary-round-1 --source-sha "$(git rev-parse HEAD)" \
  --reviewed-corpus /path/to/reviewed-corpus --result-cache /path/to/result-cache \
  --case-offset 0 --case-limit 16 \
  --model docker/stub-pages/models/model.gguf --llama-server /app/llama-server
```

The default window is 16 distinct cases; `--case-limit` permits 1..256 cases,
subject to the 2-MiB manifest budget and 64-source window limit. At most 16 misses
may invoke the model per build. The manifest's `corpus_window` reports offset,
returned count, total cases and `next_offset`; explicitly use that next offset for
the next export. Cases are ordered by their source-selection hashes; the reported
`corpus_sha256` identifies the case-key snapshot. If ingestion changes that
fingerprint, restart pagination from offset zero so newly inserted earlier keys
are not skipped; existing full-recipe cache hits still do not rerun the model.
This avoids silently reusing the first batch forever or generating
every random keyword combination. Repeating an existing window is an exact cache
check, not accumulation of duplicate model runs. Archive growth does not expand
what the browser can execute: post-build unknown input remains `not_generated`.

`manifest.build` identifies the current export. Each generated entry's
`artifact_build` identifies the original execution artifact; `reused` explicitly
distinguishes cache delivery. `model_inference_performed`, `new_generations` and
`observation_log` on the manifest describe **this build**, not past cached runs.
The manifest is `generated` when every entry is available, `not_generated` when
none are, and `partially_generated` when cache hits coexist with ungenerated
misses. A cached success remains available in a mixed no-runtime export.

## Execution observations

Actual successful completions append `portable/summary_generation` observations
through the existing `mock.common.append_jsonl` writer. These are **not** MCP
`tools/call` events. The observation records request hashes, actual server token
usage, elapsed time, stop reason and generated-text SHA-256, not raw source,
prompt or completion text. Receipt metrics are read back from those observations.
Each generated artifact binds its log SHA-256, one-based line and exact line hash.
Published `summary-logs/<sha256>.jsonl` paths keep original logs content-addressed
when receipts are reused; `summary-build.jsonl` is emitted only for new generation
in the current build. Each completed request is checkpointed with its own original
log before the next request runs. A later failure publishes no partial batch, but
an explicitly selected durable cache retains earlier valid executions for retry.
Line hashes exclude the terminating newline.
No-runtime exports create no new generation observations; reused results retain
their original historical logs. Offline tests use temporary
mock responses only to verify these contracts; their passing does not prove a
real GGUF run or model quality.

## Durable handoff and bounded real incremental check

The Docker build exports a separate `summary-handoff` target. Its
`pages/corpus/` preserves the actual selected Pages summaries and their reviewed
cases, not merely the synthetic incremental test. `pages/report.json` records
captured/missing counts. Capture independently derives eligible fixture selections
(or uses explicitly reviewed input metadata), validates the complete expected
recipe, receipt and original log bytes, and never invokes inference:

```sh
PYTHONPATH=src python3 -S scripts/check_incremental_summary_build.py capture \
  --execution /site/execution --out /pages-handoff --source-sha "$SOURCE_SHA"
```

The same build stage also runs the bounded synthetic check using its **already
prepared** GGUF and llama.cpp binary. No second model download is needed:

```sh
PYTHONPATH=src python3 -S scripts/check_incremental_summary_build.py check \
  --out /summary-handoff --source-sha "$SOURCE_SHA" \
  --model /models/model.gguf --llama-server /app/llama-server
```

The first fresh Python process generates one fixed synthetic case, then archives
its complete corpus. The second fresh process reloads that corpus, appends a
**different source**, reuses the exact first result and generates only the missing
one. That second process explicitly submits both the old and new cases, verifying
one duplicate and one genuinely novel addition. The check verifies two actual
generation observations total, one reuse, two
process IDs, and unchanged previous artifact/log bytes. Its final complete corpus
and check evidence appear under `validation/` in the separate handoff target.
Failure exits nonzero. No synthetic mock result is a fallback for real execution.

Offline unit tests exercise the same cross-process handoff using explicitly
marked temporary response stubs; passing those tests does not establish that a
model or Docker ran. The Docker check is the real execution gate. Neither tests
nor a successful integration check establish summary accuracy.

The uploaded `portable-summary-handoff` artifact is **not Pages and not necessarily
private**. It accepts only reviewed-public data. Cross-run accumulation requires
explicit retrieval and selection of its `pages/corpus/` as the next input; a
BuildKit cache alone does not provide durable hosted-run persistence. The helper's
`round` command accepts that input plus new reviewed JSONL and writes the updated
complete corpus for another handoff. See [corpus handoff](summary-corpus.md#explicit-cross-build-handoff-not-automatic-persistence)
for commands, resource bounds, coverage counts and privacy checks.
