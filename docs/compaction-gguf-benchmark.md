# Issue #115: Real llama.cpp/GGUF compaction experiment

## Measured vs. not measured

The opt-in [compaction-gguf-benchmark workflow](../.github/workflows/compaction-gguf-benchmark.yml)
downloads the existing **commit-pinned + SHA-256-checked** SmolLM2-135M-Instruct
Q4_K_M GGUF and starts a digest-pinned **llama.cpp server** locally.
This is **real GGUF inference**, NOT the `cpu_llm_lite` stand-in or OPENAI_MOCK_LOG
character-length estimate. SmolLM2 is **not Meta Llama weights**; llama.cpp is
the inference engine. All calls stay on loopback and use synthetic fixtures.

The benchmark makes matched original and `compact()` requests under:
- 2048-token configured llama.cpp context window, one server slot, two CPU threads,
  Docker restricted to two CPUs and 3 GiB.
- temperature 0, constant max 24 generated tokens, fixed seed per repetition;
  order alternates original/compacted to reduce cache/warm-up bias.
- two paired trials per four scenarios. Server-level prompt cache disabled in
  the request, but OS caches, scheduling, and execution noise still exist.
- exact real `usage.prompt_tokens` and `usage.completion_tokens` returned by
  llama.cpp; context window independently retrieved from `GET /props`.
  No characters-to-tokens approximation is used for the published reduction.
- local HTTP wall-clock round-trip latency, **not** an isolated
  prefill/decode latency and not a GPU result.
- a narrow, explicitly labeled one-word exact-match quality proxy.
  This is NOT human judgment, general reasoning quality, safety validation,
  or real-world instruction-following performance.

The report includes original and compacted prompt tokens, token reduction,
context occupancy and remaining headroom, output tokens, exact-match counts,
request latency, and mentions of an invented `exfiltrate_secret` function.
No actual functions are advertised to the model, so unsolicited tool mentions
are only a **textual anti-pattern indicator**; this experiment does **not**
establish real MCP invocation or tool-choice parity. That requires a
tool-capable model, real tool-call parsing, and separate valid vs. invented
function-call trials.

## Conservative implementation limitation

The model-facing compaction API is *unchanged*. Its out-of-band typed
provenance is never sent as a model message. When the removed prefix includes
a prior user instruction, the API intentionally declines reduction.
Three synthetic cases contain old assistant/tool exchanges but no old user
messages and exercise actual reduction. The fourth has a prior user
instruction; it exercises intentional **zero reduction**. Reports show
compacted/declined counts separately. A percentage over only compacted
cases is **not** a percentage saving for normal conversational traffic.

If a result has missing `usage`, a model failure, a reported context overflow,
a model digest mismatch, or unexpected lack of actual token savings, the
benchmark **fails** rather than publishing an estimate as measured data.

## Privacy and traceability

Only synthetic histories are submitted. Neither request bodies, injected
tool text, nor generated answers are placed in JSON evidence. The artifact
contains numeric measurements, scenario identifiers and binary pass/fail
indicators, plus separate raw pytest JUnit and the model download provenance
record. The workflow checks for known fixture leaks before uploading.

Repo-provided pins:
- GGUF: `bartowski/SmolLM2-135M-Instruct-GGUF` at
  `09816acd5d99df7be770d85ea30822623dab342c`, Q4_K_M,
  SHA-256 `2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d`.
- llama.cpp: `ghcr.io/ggml-org/llama.cpp:server-b11669@sha256:8ffe0d28ff5261cc2f78ca1f67db1515bc2529b904a3c25f5b24f4cedfc3411d`.

## Running it

Use the Actions workflow for repeatable CPU isolation and JUnit evidence.
It runs on benchmark-code PR changes, and also supports a manual
`workflow_dispatch`. Normal `pytest` does not fetch model weights.

To run locally with Docker:

```bash
python scripts/fetch_tiny_cpu_gguf.py docker/stub-pages/models/model.gguf
docker run --detach --name compaction-gguf --cpus=2 --memory=3g \
  -p 127.0.0.1:8080:8080 \
  -v "$PWD/docker/stub-pages/models:/models:ro" \
  ghcr.io/ggml-org/llama.cpp:server-b11669@sha256:8ffe0d28ff5261cc2f78ca1f67db1515bc2529b904a3c25f5b24f4cedfc3411d \
  -m /models/model.gguf --host 0.0.0.0 --port 8080 \
  --ctx-size 2048 --parallel 1 --threads 2
# Wait for curl -f http://127.0.0.1:8080/health
PYTHONPATH=src python -m mcp_toolcall_lab.compaction_gguf_benchmark \
  --out test-results/compaction-gguf-results.json \
  --model-file docker/stub-pages/models/model.gguf \
  --expected-sha256 2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d
docker rm -f compaction-gguf
```

## Interpreting a result

Use `aggregate.token_reduction_fraction_compactable` with the
`compacted_samples` denominator and `declined_samples` beside it.
`context_fraction` is **prompt tokens / server-reported context window**;
`context_headroom_tokens` subtracts prompt and generated tokens.
Compare the paired latency results without treating either difference as
a guaranteed throughput improvement. Quality scores are exact string
matches; read them as a small functional smoke, not proof of quality parity.

The wider Issue #115 remains open for user-constraint-preserving compression,
proxy integration, and real multi-turn MCP/tool-choice and injection studies.
