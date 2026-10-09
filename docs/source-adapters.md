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
