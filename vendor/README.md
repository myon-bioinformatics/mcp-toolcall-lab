# Vendored `markdown.py`

Pinned snapshot of [`myon-bioinformatics/markdown`](https://github.com/myon-bioinformatics/markdown)
`markdown.py` (stdlib helpers, not a CommonMark engine). The stub front loads
this file by path so the Pages/Docker image does not grow a pip dependency.

Identity and byte hashes live in the repo-root [`vendor.lock.json`](../vendor.lock.json)
entry for `vendor/markdown.py`. Refresh by updating that lock entry and the
checked-in bytes together; `tests/test_mcp_toolcall_lab_vendor_lock.py` and
`mcp_toolcall_lab.markdown_lib.assert_markdown_provenance()` guard drift.

# Vendored `ascii_artist.py`

Pinned source snapshot from `myon-bioinformatics/ascii_artist`. It stays stdlib-only
and is used only as an optional presentation adapter (not article parsing).

# Vendored `gh_ops.py`

Pinned snapshot of [`myon-bioinformatics/myon-bioinformatics`](https://github.com/myon-bioinformatics/myon-bioinformatics)
`gh_ops.py` (stdlib-only GitHub REST operations). Loaded by path by
[`src/mcp_toolcall_lab/github_ops_server.py`](../src/mcp_toolcall_lab/github_ops_server.py)
— see [`docs/github_ops_mcp.md`](../docs/github_ops_mcp.md).

Pins and hashes: `vendor.lock.json` entry `vendor/gh_ops.py`.

# Vendored `gh_identity.py`

Pinned snapshot from [`myon-bioinformatics/gh_identity`](https://github.com/myon-bioinformatics/gh_identity).
`mcp_toolcall_lab.mcp_toolcall_lab_repository_diagnostics` builds repository metadata v1 records
through `validate_repository_metadata_v1` / `repository_metadata_v1` from this
module (the retired Ironmate `repository_metadata_contract.py` shim is not vendored).

Pins and hashes: `vendor.lock.json` entries under `vendor/gh_identity*`.

## Shared screenshot checks in CI

The stub-pages job checks out browser-test-kit separately at
`6a2e32a4bbe49be5268e6b30040d665a89eecf66` and uses its stdlib receipt writer/validator
for all three required static Pages PNGs, including recorded bytes/hash and run identity. Live Pixiv PNGs are validated only when
the existing live-fetch classification succeeds; 403/429 skips keep their warning
and are not counted as live evidence. Existing WebKit shutdown diagnostics remain.
See the [shared screenshot guide](https://github.com/myon-bioinformatics/browser-test-kit/blob/6a2e32a4bbe49be5268e6b30040d665a89eecf66/docs/screenshot-evidence.md).
Static/live sets have separate receipts. Failed/skipped receipts cannot cover success;
actual bundle copies must reject image/hash/run/stage corruptions.
The current capture lane measures desktop Chromium and mobile WebKit emulation;
Firefox is not measured. Observations continue to upload under `if: always()`.

# Vendored `web_ui.py`

Pinned snapshot of [`myon-bioinformatics/web-ui`](https://github.com/myon-bioinformatics/web-ui)
(`PySaiyanWeBoo`) `web_ui.py` and its LICENSE (stdlib-only). The Pages `#pokemon`
panel is web-ui's shared **source panel**: `stub_front.load_web_ui()` loads this file
by path, `source_panel_html(spec)` writes the panel, and `source.js` / `source.css` come
from the same module. This repo keeps only the JSON spec,
[`fixtures/pokeapi/pokeapi.spec.json`](../fixtures/pokeapi/pokeapi.spec.json) — see
[`docs/stub_pages.md`](../docs/stub_pages.md).

Pins and hashes: `vendor.lock.json` entries `vendor/web_ui.py` and `vendor/web-ui-LICENSE`
(registered in the parent's `vendor-consumers.json`).

# Vendored `vendor_fleet_plan.py`

Pinned snapshot of the parent repository's stdlib-only fleet evidence planner. The `fleet_query` MCP tool loads this module by path and delegates `deps`, `who`, `facts`, `why`, and `reverify` queries against the parent's published `fleet.jsonld`. The lock pins the reviewed parent commit and records the source blob and SHA-256; the module is never imported from the package path.
