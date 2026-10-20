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

`mcp_toolcall_lab.adapters.engine_adapter` implements upstream engine/model distillation:

- **1-Pass Upstream Distillation**: Instead of calling heavy external tools/models on every turn, `distill_engine_adapter` extracts and processes evaluation features, model weights specs (e.g., Shogi AI NNUE / GGUF CPU models / Hugging Face Hub / Docker specs), and runtime bounds into a compact `DistilledAdapter` artifact.
- **Cache Export & Integrity**: `export_adapter_cache` and `load_adapter_cache` handle JSON serialization with SHA-256 checksum verification and provenance metadata.
- **Lightweight Execution**: `execute_via_adapter` executes evaluation/inference using the cached distilled features, bypassing heavy upstream calls or GPU requirements for fast downstream CI / subsequent MCP execution.

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

Public function signatures, `DistilledAdapter` fields and schema `1.0` JSON shape
are unchanged. The earlier PR implementation hashed only source and features:
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
