# Source adapter ownership

Transferred from Ironmate main `a24e356503b73b7daf1537c6e5f26a0426a6d876`:
`source_adapter.py`, `niconico_adapter.py`, the offline niconico fixture and tests.
Ironmate cleanup PR #81 retires its copies; coordinate landing of the transfer
before that removal. Tracking: #107, related historical integration plan #39.

Import `mcp_toolcall_lab.adapters.niconico_adapter`. The provider adapter owns URL
encoding, validation, HTTP errors, pagination and before/after snapshot-version
observation. `source_adapter` supplies only provider-neutral URL/provenance helpers.
The caller owns tool registration, MCP transport and publication. No network work
occurs at import; no new live MCP tool is registered by this transfer.

These modules use stdlib only. Default tests patch the network and preserve the
existing pagination/version/error checks. MCP/API routing remains follow-up #107.
Existing Ironmate fixtures are historical offline contracts; they are not evidence
that Ironmate still serves a live catalog or MCP endpoint.

## Upstream Distillation Engine Adapter (`engine_adapter.py`)

`mcp_toolcall_lab.adapters.engine_adapter` stores **caller-supplied observations**
and an upstream specification. The historical `distill_engine_adapter` name does
not mean model weights are distilled. HF/Docker/local specifications are metadata;
no upstream engine is initialized, downloaded or executed. `runtime_bounds` are
stored declarations, not enforced resource limits. Core imports need only Python's
standard library, with no MCP or external inference engine.

### Execution contract 2.0 / migration from implicit fallback

Cache JSON remains schema `1.0`; `EngineSpec` / `DistilledAdapter` fields and the
two positional execution arguments are preserved. Known observation selectors
still return `status="ok"` and the original `evaluation`. Legacy result keys remain,
with these intentional corrections identified by `result_contract_version="2.0"`:

| Case | `status` | `execution_mode` | `cache_hit` |
|---|---|---|---|
| Stored query/position key (including falsy stored values) | `ok` | `cached_observation` | `true` |
| Unknown key, default policy | `cache_miss` | `unsupported` | `false` |
| Unknown key, explicit `on_miss="stored_default"`, stored default available | `ok` | `approximation` | `false` |
| Unknown key, opt-in but no stored default | `cache_miss` | `unsupported` | `false` |
| Invalid/unsupported selector | `unsupported` | `unsupported` | `false` |
| Expired snapshot with caller-specified TTL | `cache_miss` | `unsupported` | `false` |

`actual_model_inference` is reserved for a future genuine backend. This module
never emits that mode: every receipt has `model_inference_performed=false` and
`upstream_bypassed=true`. An actual-model request such as `{"prompt": "..."}` is
unsupported. A key whose observation contains model-like text is still replay.
`device_used="unknown"` means no inference device was measured; the upstream
specification is reported separately as `device_requirement`, even for `gpu`.
`execution_ms` measures only this local lookup/receipt operation, not model latency.

Inputs select observations with **one nonempty string** `query` or `position`.
`{}` or the literal selector `"default"` selects the stored default observation
itself. Both selectors together, nonstrings, empty strings and additional fields
are unsupported, rather than silently ignoring inference settings. This is a
caller-defined key lookup, **not** a full input/configuration fingerprint or
semantic cache. Callers own key uniqueness and the meaning of each observation.

```python
from mcp_toolcall_lab.adapters.engine_adapter import execute_via_adapter

known = execute_via_adapter(adapter, {"position": "startpos"})
missing = execute_via_adapter(adapter, {"position": "unseen"})
assert missing["status"] == "cache_miss"  # evaluation is None; inspect error.code

# Explicit migration if the old default behavior is appropriate for the consumer:
approx = execute_via_adapter(
    adapter, {"position": "unseen"}, on_miss="stored_default",
)
# Check execution_mode before treating any evaluation as an exact observation.
```

The deterministic approximation `stored_default_constant_v1` returns the supplied
`default` unchanged for **every** unknown selector. It does not estimate similarity
or generate a score, best move or confidence. Its reference is
`evaluation_features.default`, reported in `result_source` and `matched_key`;
`cache_hit=false` refers to the requested selector. The receipt explicitly lists
these limitations and sets measured accuracy/error bounds to unknown (`None`),
with `calibrated=false`. Any nested confidence is caller-supplied data, not a
validated probability. No claim of agreement with a model, accuracy or speedup is
made. A missing default never generates the former synthetic `0.0/0.5` pair.

Consumers must check `status` before reading `evaluation`; errors keep evaluation
`None` with `error.code`. Opt-in is required to retain the old default substitution.
There is no compatibility switch restoring fabricated values or a false device.
Invalid policy arguments raise `ValueError`.

Pass `max_age_seconds=60` to reject snapshots at or beyond that age. TTL is opt-in
(no expiry by default for existing caches) and applies to known keys **and**
approximations. Age is measured from timezone-aware `created_at`, the artifact's
creation time, not the original observation time. A malformed, naive or future
timestamp fails closed when TTL is requested. Re-export does not refresh creation
time. TTL cannot detect observations already stale when the artifact was created;
source/model/tokenizer revision invalidation remains the caller's responsibility.
Returned evaluations are copied so editing a receipt cannot change the cache.

### P0 reuse / scope audit (#147)

| Existing asset | Decision | Added scope / verification |
|---|---|---|
| EngineSpec / DistilledAdapter schema 1.0 | Reuse unchanged | Add versioned execution receipts, explicit miss policy and optional age check |
| Whole-artifact checksum / provenance | Reuse | Existing tamper tests plus malformed JSON/shape and stale snapshot tests |
| Stored default observation | Reuse with explicit opt-in | Constant approximation; deterministic evaluations tested separately from timing |
| CLI/MCP, xprobe, LittleBit, min.py/min.js | Leave as-is in P0 | No duplicate discovery, facade, estimator or bundler; direct Python stdlib path tested with `python -S` |
| Real GGUF/ONNX engines | Defer to P1 | No model execution/device/benchmark claim in P0 |

Standalone example (from the repository root):

```sh
PYTHONPATH=src python -S -c 'from mcp_toolcall_lab.adapters.engine_adapter import load_adapter_cache, execute_via_adapter; print(execute_via_adapter(load_adapter_cache("adapter.json"), {"query": "known"}))'
```

Focused validation: `PYTHONPATH=src python -m pytest tests/test_engine_adapter*.py`.
No new dependencies, workflows, cache format or transport are introduced.

### Cache checksum contract and compatibility

The SHA-256 covers the **complete JSON cache object except its top-level `sha256`
field**, including schema version, identity, source specification, evaluation
features, runtime bounds, provenance and creation timestamp. Nested fields named
`sha256` and unknown extra fields are also covered. Canonical serialization uses
Python `json.dumps(payload_without_sha256, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)`, encoded as UTF-8. Object key order, JSON
indentation and Unicode escape spelling do not matter; array order does.
Distillation, export and verified loading use this same calculation. Export
recomputes the checksum for the snapshot being saved (nested dictionaries can
change after construction), without mutating the supplied frozen adapter.
Non-finite numbers are rejected by canonical serialization.

`DistilledAdapter` fields and schema `1.0` JSON shape are unchanged. Execution
adds keyword-only policy options and the versioned receipt described above. The earlier PR implementation hashed only source and features:
those legacy digests are deliberately rejected by default with a checksum
mismatch, rather than silently accepting unprotected metadata. Prefer rebuilding
the adapter from trusted inputs. To migrate a legacy cache **only when its entire
contents have been independently trusted**, explicitly load it with
`load_adapter_cache(old_path, verify_checksum=False)`, then call
`export_adapter_cache(adapter, new_path)` and verify the new file normally.
Disabling verification provides no integrity check; re-exporting cannot establish
the trustworthiness of the old contents.

An embedded SHA-256 detects inconsistency with the stored digest, **not producer
authenticity**. An attacker able to edit the payload and recompute its digest can
make a modified cache pass verification. Provenance is covered metadata, not proof
of origin. Authenticity requires a separately trusted signature, MAC, or externally
pinned digest obtained through a trusted channel.

This adapter stores caller-supplied observations and specifications. These cache
tests do not establish live Hugging Face Hub access, Docker execution, model
weight distillation, or inference by an actual upstream engine.
