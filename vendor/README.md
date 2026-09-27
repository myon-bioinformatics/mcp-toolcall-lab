# Vendored `markdown.py`

Pinned snapshot of [`myon-bioinformatics/markdown`](https://github.com/myon-bioinformatics/markdown)
`markdown.py` (stdlib helpers, not a CommonMark engine). The stub front loads
this file by path so the Pages/Docker image does not grow a pip dependency.

This file owns Markdown ↔ HTML/CSS and the thin Markdown ↔ Kramdown IAL
subset (`markdown_to_html` / `html_to_markdown` / `default_stylesheet` /
`markdown_to_kramdown` / `kramdown_to_markdown` / `ial` / `with_attributes`).
The lab does not keep a second copy of those converters.

Provenance is in [`markdown.provenance.json`](markdown.provenance.json)
(`commit` + git `blob_sha` + `sha256`). Refresh **that commit**, not `main`:

```bash
COMMIT=$(python3 -c "import json; print(json.load(open('vendor/markdown.provenance.json'))['commit'])")
gh api "repos/myon-bioinformatics/markdown/contents/markdown.py?ref=${COMMIT}" --jq .content \
  | base64 -d > vendor/markdown.py
python3 -c "from mcp_toolcall_lab.markdown_lib import assert_markdown_provenance; assert_markdown_provenance()"
```


# Vendored `ascii_artist.py`

Pinned source snapshot from `myon-bioinformatics/ascii_artist`. It stays stdlib-only
and is used only as an optional presentation adapter (not article parsing): its
`to_web_ui_v1_html()` emitter provides the shared web-ui HTML contract v1 surface
for Wikipedia/pixiv Encyclopedia results. Article extraction and section semantics
remain owned by their source adapters and `markdown.py`.

# Vendored `gh_ops.py`

Pinned snapshot of [`myon-bioinformatics/browser-test-kit`](https://github.com/myon-bioinformatics/browser-test-kit)
`scripts/gh_ops.py` (stdlib-only GitHub REST operations; no `gh` CLI). It is
loaded by path, the same way as `markdown.py`, by
[`src/mcp_toolcall_lab/github_ops_server.py`](../src/mcp_toolcall_lab/github_ops_server.py)
— see [`docs/github_ops_mcp.md`](../docs/github_ops_mcp.md). No function in
this file is reimplemented at the call site; the FastMCP tools are thin
wrappers over `Client`/`pr_status`/`pr_for_branch`/`open_prs`/`issue_comments`/
`runs`/`url_*` exactly as vendored.

Provenance is in [`gh_ops.provenance.json`](gh_ops.provenance.json)
(`commit` + git `blob_sha` + `sha256`). Source PR:
[myon-bioinformatics/browser-test-kit#10](https://github.com/myon-bioinformatics/browser-test-kit/pull/10)
(branch `claude/gh-ops-cli`, open, not merged as of the pinned commit).
Refresh **that commit**, not `main`, once the upstream PR lands:

```bash
COMMIT=$(python3 -c "import json; print(json.load(open('vendor/gh_ops.provenance.json'))['commit'])")
gh api "repos/myon-bioinformatics/browser-test-kit/contents/scripts/gh_ops.py?ref=${COMMIT}" --jq .content \
  | base64 -d > vendor/gh_ops.py
pytest -q tests/test_gh_ops_provenance.py
```
