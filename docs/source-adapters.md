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
- **Extreme 1-Bit / Ternary & LoRA Quantization**: Support for 1-bit / sub-byte Qualcomm-style quantization (`create_1bit_distilled_adapter`, `convert_to_1bit_weights_delta`) and LoRA delta adapters (`create_lora_adapter`), distilling GPU-heavy models into bitwise/popcount CPU-class artifacts.
- **Cross-Model Reusability**: `can_apply_adapter` evaluates whether a distilled adapter artifact can be reused across different model architectures or quantized model variants.
- **Cache Export & Integrity**: `export_adapter_cache` and `load_adapter_cache` handle JSON serialization with SHA-256 checksum verification and provenance metadata.
- **Lightweight Execution**: `execute_via_adapter` executes evaluation/inference using the cached distilled features, bypassing heavy upstream calls or GPU requirements for fast downstream CI / subsequent MCP execution.
