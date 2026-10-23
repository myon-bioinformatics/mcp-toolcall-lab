# Incremental, local summary corpus

The corpus is a local append-only collection of **different source inputs, selections,
and actual generated results**, not a fixed random example repeated each build.
The sampler seed controls one generation parameter. It does not freeze the input
collection, establish quality, or make execution deterministic across machines.

Nothing in this subsystem uploads, publishes, downloads models, schedules work,
or launches inference. Browser-entered text stays in that browser unless its user
explicitly downloads it. A browser export is **not** approval to publish it.
The default build may seed the reviewed synthetic fixture; later rounds can supply
an explicitly reviewed corpus and reuse an explicitly selected local result cache.

## Local request ingestion

The input is an ordinary exported `lab-execution/1` `source_selection` request,
containing exactly `schema`, `operation`, and `input`. The input contains `text`,
`keywords`, and `sentence_ids`. Source-bound IDs come from the shared JS analyzer;
IDs cannot be reused after editing the text. There must be selected evidence.

Run from the checkout, using an environment with Python and Node available:

```sh
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_corpus \
  --corpus /path/to/private-local-corpus ingest /path/to/source-selection.json \
  --request-id local-round-001
```

This persists a private, **unreviewed** case. Keep local corpus directories outside
any checkout or deployment output unless intentionally preparing public data.
The subsystem does not add ignore rules or secure your storage for you.

The immutable case contains:

- Schema `lab-summary-corpus-case/1` and a caller-provided request ID.
- The exact source-selection request and its canonical SHA-256 content key.
- Source SHA-256 and UTF-8 byte count.
- `offset_unit: "utf16_code_units"` and selected sentences, including their original
  IDs, start/end offsets, and verbatim evidence.
- `review: null` by default, or an explicit public-review declaration.

The same source and selected keyword/sentence sets deduplicate even if the caller
changes the request ID or reorders the selection arrays. The first request ID
remains provenance metadata. After the shared analyzer validates all source-bound
IDs, keyword IDs are stored in original first-occurrence order and sentence IDs in
original-source order. Exact source text is preserved byte-for-byte: source edits,
different keyword sets, or different selected-sentence subsets remain distinct
cases. There is no whitespace, case, or Unicode normalization, and invalid/stale IDs
are rejected rather than repaired. This canonical case identity is separate from
the full model-specific summary recipe hash. Each request ID must match
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`.

## Explicit public review

Only after a person has checked the text and selection for privacy and publication
rights, ingest into a **separate reviewed corpus**, explicitly asserting that review:

```sh
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_corpus \
  --corpus /path/to/reviewed-public-corpus ingest /path/to/reviewed-selection.json \
  --request-id public-round-001 --reviewed-public-by reviewer-name \
  --reviewed-at 2026-10-23T00:00:00+00:00
```

This is a local declaration, not authentication, permission discovery, or automatic
publication. Public review is immutable with the case. Re-ingesting a private case
with changed review status is rejected rather than silently promoting private data;
use a separate, deliberately reviewed corpus/export. Public-build loading rejects
any unreviewed case in the supplied source. Do not label private input reviewed just
to bypass this check.

Export and import case records, preserving their review status:

```sh
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_corpus \
  --corpus /path/to/reviewed-public-corpus export-jsonl /path/to/new-reviewed-cases.jsonl
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_corpus \
  --corpus /path/to/another-local-corpus import-jsonl /path/to/new-reviewed-cases.jsonl
```

Exports require a new filename. Import is incremental: earlier valid rows may
already have been appended when a later row fails. It reports failure rather than
silently skipping corruption. Correct the input and retry; identical cases dedupe.
Case exports contain input and selection, not generated results. Retain the complete
local cache directory to retain previous results and their observation logs.

## Repeated build rounds and missing-only execution

The build accepts an explicitly reviewed JSONL file or corpus directory and a local
persistent result cache:

```sh
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_build \
  --out /path/to/fresh-build-round-001 --source-sha FULL_40_CHARACTER_COMMIT \
  --reviewed-corpus /path/to/reviewed-public-corpus \
  --result-cache /path/to/persistent-local-results --case-offset 0 --case-limit 16
```

Without model/runtime arguments this is a non-generating export: verified existing
results can be reused, and misses honestly remain `not_generated`. For an authorized
real build, provide the already verified local `--model` and `--llama-server` paths
as described in [portable execution](portable-execution.md). This corpus module
itself never executes a model. New successful executions are checkpointed to the
explicit local cache; retrying a partially failed generation batch only generates
missing exact requests. A failed build does not publish partial public receipts.

The requested window is deduplicated and ordered by source-selection SHA-256.
The manifest's `corpus_window` reports `offset`, `limit`, `returned_cases`,
`total_cases`, `has_more`, and `next_offset`. Advance to `next_offset` instead of
rebuilding the first examples forever. The window helper also returns
`corpus_sha256`, a fingerprint of the ordered distinct content keys. Adding cases
may move offsets, so treat pagination as a snapshot; keep the reviewed input fixed
while traversing its windows or restart against the changed fingerprint. No
automatic schedule or external write is installed.

Only the selected bounded window is returned in memory (1–256 cases). At most 64
sources are projected per build; each public source is at most 4 KiB. Actual missing
generations remain limited to 16 per build; selected evidence is limited by the
build to 8 sentences/2 KiB. A manifest exceeding 2 MiB fails before inference.
A disposable on-disk SQLite index supplies stable ordering and deduplication without
loading the full source corpus in RAM. It is removed when the window operation ends.

A result key is SHA-256 of the **entire summary request**, including source identity,
selected keywords, exact evidence/offsets, model identity and checksum, runtime,
prompt/version, all explicitly recorded sampler/generation/server settings, and
remaining-defaults declaration. Reusing the same seed alone never produces a hit.
Changing the prompt or model, for example, produces a different key. Stored older
recipes remain readable, but do not match a newer build's expected full recipe.

Each successful result retains its original build, generated text checksum,
usage/timing/finish data, wire-request checksum, and original observation-log bytes.
Public exports use `summaries/<summary-request-sha256>.json` and
`summary-logs/<log-sha256>.jsonl`. The log's checksum and exact referenced line are
checked against the receipt. These checks establish content consistency, not
producer authenticity, factual correctness, confidence, or legal permission.
No failure, fallback, or mock completion is stored as a genuine result by the
production build. Synthetic test objects exist only inside temporary unit tests.

## Append-only storage, archiving, and lazy retrieval

A corpus directory contains:

- `shards/<opaque-shard-id>.jsonl`: bounded append-only record shards.
- `index/{cases,results,logs}/<prefix>/<content-key>.json`: immutable key references
  with the shard ID, byte offset, row length, and row SHA-256.
- `archives/<opaque-shard-id>.zip`: one sealed JSONL shard per archive.
- `active.json`: small mutable append position; `.writer.lock` serializes writers.

Records use schema `lab-summary-corpus-record/1`. Case, result, and log payloads are
versioned separately. New content appends to JSONL; duplicates do not grow the
collection. The original whole observation log is itself content-addressed and
stored once even when multiple receipts reference it. Existing result keys cannot
be overwritten by different generated outputs or changed build metadata.

```sh
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_corpus \
  --corpus /path/to/persistent-local-results archive
PYTHONPATH=src python -m mcp_toolcall_lab.portable_summary_corpus \
  --corpus /path/to/persistent-local-results check
```

`archive` seals the active shard and archives at most 16 shards per call. Repeat
explicitly for remaining shards. Archiving verifies the decompressed bytes against
the source before deleting that local uncompressed shard. Key references do not
change. Subsequent writes start a new shard; archived results remain reusable.
`SummaryCorpus.get_result(key)` follows one on-disk reference and reads only the
required bounded record and its log, not the entire corpus. ZIP offsets may require
streaming through the preceding bytes of **one bounded member**. Nothing is
extracted to the filesystem. Unrelated broken archives do not prevent a keyed read;
`check` performs a full streaming audit and reports corruption anywhere.

Limits are explicit: 2 MiB per JSONL record, 8 MiB/256 records per shard, 1 MiB per
original observation log (at most 1,000 rows, matching the browser/Node reader), 64 MiB per JSONL import file, and one archive member.
ZIP preflight checks reject oversized central directories, excessive entry counts,
ZIP64/multidisk layout, unexpected names, traversal, symlink members, encryption,
unsupported compression, excessive declared sizes, and extreme compression ratios.
All artifact, receipt, runtime, build, recipe and observation fields use closed schemas;
every row of a reused log is validated, including rows not referenced by this receipt.
Unrecognized metadata is rejected rather than copied into a public artifact.
Strict readers reject duplicate JSON keys, blank/broken/unterminated lines, invalid
Unicode, nonfinite/unsafe numbers, unknown schemas, and excessive structure depth.
Lookups also verify row boundaries, lengths, hashes, source IDs, evidence, and logs.

## Interruptions and operational limits

This is a local single-writer storage format, not a database service or an
adversary-resistant authentication mechanism. Reads are bounded and writes use a
writer lock, fsync for appended rows, and atomic index/state replacement. It does
not promise a multi-file transaction or recovery after power loss. A process killed
between steps may leave a stale lock, an orphan row, an incomplete active state, or
both compressed and uncompressed copies. Such inconsistencies should be audited,
not automatically skipped, overwritten, or guessed away. Back up the directory,
ensure no writer is running, and inspect/reconcile interrupted files before
removing a stale lock or retrying. No automatic destructive repair is provided.
Do not mutate the same directory concurrently outside this API.

## Python API

```python
from mcp_toolcall_lab.portable_summary_corpus import SummaryCorpus

corpus = SummaryCorpus('/local/corpus')
case = corpus.ingest_request(selection_request, 'round-002')  # private by default
case = corpus.get_case(source_selection_sha256)
for case in corpus.iter_cases():
    pass  # streaming, strict validation
artifact = corpus.get_result(full_summary_request_sha256,
    expected_request=prepared_summary_request, expected_selection=selection_request)
# Only after successful real generation and observation-log creation:
corpus.put_result(artifact, observation_log_bytes=original_log_bytes)
log_bytes = corpus.get_observation_log(log_sha256)
corpus.archive()
counts = corpus.check()
```

`validate_summary_artifact`, `load_verified_cached_artifact`, `iter_jsonl`,
`iter_reviewed_requests`, and `load_reviewed_corpus_window` are available for local
integration. All raise `CorpusError` on invalid/unsafe content. A missing keyed
case/result returns `None`; corrupt data is never converted to a cache miss.

## Explicit cross-build handoff (not automatic persistence)

A BuildKit cache can disappear with the runner. It is **not** the durable input to
another hosted run. `scripts/check_incremental_summary_build.py` exports an
independently reloadable corpus with every archive, content index and original
observation log needed for exact reuse. Nothing automatically downloads a previous
Actions artifact or approves new browser text.

The separate `portable-summary-handoff` Actions artifact contains:

- `pages/corpus/`: the reviewed cases and actual results selected in the Pages
  build. This is the primary input to subsequent accumulation.
- `pages/report.json`: selected/captured/missing counts and the corpus snapshot
  hash. Capture performs no inference and does not create generation observations.
- `validation/corpus/`: two different synthetic check cases and their results.
- `validation/report.json`, `validation/round-1/`, `validation/round-2/` and
  `validation/reviewed-inputs/`: the bounded clean-process check and its evidence.

This is a **non-Pages artifact, not private storage**. Actions artifacts in a public
repository may be accessible to others. Every transferred case must already be
reviewed public. Never upload a private local corpus merely because it is outside
Pages. The complete helper output is not a deployable static-site directory.

To capture an existing build without running the model again:

```sh
PYTHONPATH=src python -S scripts/check_incremental_summary_build.py capture \
  --execution /path/to/site/execution --out /path/to/fresh-pages-handoff \
  --source-sha FULL_40_CHARACTER_COMMIT
```

By default eligible selections are derived independently from the checksum-pinned
public fixture, not from a manifest's assertion of review. For another reviewed
corpus pass `--reviewed-corpus PATH` and the same `--case-offset`/`--case-limit` as
its export. Its original review metadata is retained. Only exact expected
full-recipe receipts and verified log bytes enter the new corpus. Unrelated files,
manifest fields and model files are not copied. Missing results are counted as
missing, never invented. Capture retains only the selected export window; it does
not merge all prior windows or claim an unselected historical corpus was saved.

After explicitly retrieving and checking the artifact you intend to reuse, run a
new process with that complete input and newly reviewed case records:

```sh
PYTHONPATH=src python -S scripts/check_incremental_summary_build.py round \
  --input-corpus /path/to/restored-handoff/pages/corpus \
  --append-reviewed-jsonl /path/to/new-reviewed-cases.jsonl \
  --out /path/to/fresh-next-round --source-sha FULL_40_CHARACTER_COMMIT \
  --model /path/to/already-verified-model.gguf --llama-server /path/to/llama-server
```

The input is read-only. `fresh-next-round/corpus/` is the **updated complete corpus**
to retain for the next round; `fresh-next-round/execution/` is only that round's
bounded static projection. Omit the model/runtime pair for an honest cache-only
round. Existing exact results keep original execution timestamps, artifact bytes
on re-export and original log bytes. Newly selected missing recipes alone execute.
A generation failure exits nonzero and cannot produce a successful round report.

Reports separate submitted additions, unique canonical additions, duplicate
additions (including already-stored cases), newly added cases, actual new
generations and reused results. `case_coverage` counts unique cases, distinct source
hashes and keyword-count/selected-sentence-count buckets. These are objective
breadth counts, not semantic categories, answer accuracy or model confidence.
Reordered keyword/sentence arrays normalize to the same corpus case. Source text
is not rewritten; changing text or evidence remains a different case.

Handoff bounds are 512 stored cases, 4096 files and 64 MiB total; an additions JSONL
is at most 4 MiB/256 records. Each export window remains 1..256 cases with at most
16 new generations. Advance the reported window explicitly as described above.
Only validated corpus storage files transfer. Unknown files, symlinks, stale
writer locks, unreviewed cases, orphan results and corrupt records are rejected
before copying. ZIP comments, member extra fields, preambles, gaps, trailing data
and unused compressed payload are rejected before transfer, even if the contained
JSONL records would otherwise validate. No arbitrary archive is extracted.
