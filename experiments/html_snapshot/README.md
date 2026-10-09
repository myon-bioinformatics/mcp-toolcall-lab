# One snapshot, repeated extraction experiment

Tracking: https://github.com/myon-bioinformatics/mcp-toolcall-lab/issues/107

Historical initial experiment; the CLI now delegates to the shared parser in
`src/mcp_toolcall_lab/adapters/html_snapshot.py`. See `docs/source-access.md`
for the current profiles and multi-article contract.

The initial Python stdlib-only offline prototype: This is not yet a production site adapter
or browser-equivalent DOM parser. It selects the first article, otherwise main,
otherwise the document, and returns text, headings, HTTP(S) links, structural
attributes and stylesheet references. It does not fetch links/assets or execute
scripts. Multiple articles and malformed HTML need a provider-specific contract.

```sh
PYTHONPATH=src python experiments/html_snapshot/extract.py snapshot.html --url https://example.test/page
python -m pytest experiments/html_snapshot/test_extract.py -q
```

## Measured public GitHub HTML

One terminal curl GET of https://github.com/myon-bioinformatics/mcp-toolcall-lab
returned HTTP 200, 452069 bytes on 2026-10-08. No retries or redirects were enabled.
SHA-256: a5ef1703f78e13a7a1fcd77c52d399ff7b14c82276b492bbe1840d2fa16031a9.
The raw response is retained in local scratch for this experiment, not committed:
GitHub page markup can include transient request/session-related fields.

Observed structure: `main#js-repo-pjax-container` contains
`article.markdown-body.entry-content.container-lg` for README content.
From that saved response: 29391 normalized text characters, 25 headings, 53
HTTP(S) link occurrences (including heading anchors), 26 stylesheet references.
No stylesheets or linked pages were fetched by the extractor. Changing selectors
or extraction requests reuses the same snapshot. Three synthetic offline tests
passed; no network access is present in the extraction implementation.

A separate cloud-browser navigation was also attempted and timed out; it is not
counted as the successful single-GET experiment. This does not establish that
Pages cross-origin fetch works, or that JavaScript-rendered content was captured.
Search result pages, computed CSS and other providers remain untested.

Next: provider-specific selection, explicit missing/ambiguous field results,
snapshot provenance/encoding, and fixtures for a search-results page. Fallback
selectors must be tried locally on the same document before any new request.
