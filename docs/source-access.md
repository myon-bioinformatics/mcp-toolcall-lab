# Source access: one retrieval, repeated local extraction

Issue #107. The goal is useful access to public information even without a
provider API. This first slice puts existing Wikipedia API calls and saved HTML
extraction behind `Registry.invoke(provider, operation, arguments)`. It is a
Python/stdlib layer, independent of MCP, Node, Playwright and browser launch.
Existing MCP catalog/tool names remain unchanged; no generic network tool is
registered on the server. Thin MCP/UI wrappers can call selected operations later.
Niconico ownership transfer stays in PR #108; this slice does not depend on it.

## Operations

| Provider / operation | Input | Behavior |
| --- | --- | --- |
| `html / extract` | `snapshot`, optional `profile` | Offline extraction, hash checked, no requests |
| `wikipedia / article` | `title`, optional `lang` | Delegates to existing API implementation and TTL/LRU cache |

Registration is trusted Python code. Unknown provider/operation, duplicate
registration and invalid argument names are errors. Request JSON contains data,
not Python or JS code. The registry returns `{provider, operation, data}` and
preserves provider-specific result fields; it does not invent completeness for
Wikipedia or silently flatten its headings. Existing Wikipedia MCP calls are
unmodified. The new registry requires `allow_network=True` for API operations,
even if the underlying cache happens to be warm. There is no paid inference.

## Capture once (explicit local CLI)

From an installed checkout, or with `PYTHONPATH=src`:

```sh
python -m mcp_toolcall_lab.source_access snapshot.json --fetch-url https://github.com/myon-bioinformatics/mcp-toolcall-lab --allow-network
```

Capture uses one GET, a 15-second urllib timeout, a 2 MiB response bound, and no
redirect/retry/link/stylesheet requests. The timeout is a socket-operation timeout,
not a strict end-to-end deadline. Existing destinations are rejected *before*
requesting. Refresh uses a new filename, preserving the previous snapshot.
HTTP errors remain errors (403 is not relabeled as proof of login required).
HTML content types only; charset from Content-Type, UTF-8 fallback, strict decode.
Compressed responses are rejected; `Accept-Encoding: identity` is requested.
This deliberately small transport does not perform browser/meta charset sniffing.
No cookies/authentication are configured by this capture helper.

`html-snapshot/1` stores decoded HTML, URL, fetch time, declared encoding,
SHA-256 of received body bytes and SHA-256 of UTF-8 decoded text. The latter is
verified before extraction. Hashes detect content drift, not source authenticity:
metadata and hashes are not signed. A pasted/saved input can use `make_snapshot()`
with `fetched_at=None`; it must not be presented as freshly downloaded.
Snapshots can contain transient page fields: keep real captures locally, and use
reviewed/synthetic fixtures for shared CI. No visitor data is auto-uploaded.

## Reuse without further access

```python
import json
from pathlib import Path
from mcp_toolcall_lab.source_access import default_registry

snapshot = json.loads(Path('snapshot.json').read_text(encoding='utf-8'))
request = {
    'provider': 'html', 'operation': 'extract',
    'arguments': {'snapshot': snapshot, 'profile': 'github-readme'},
}
Path('request.json').write_text(json.dumps(request), encoding='utf-8')
result = default_registry().invoke(**request)  # no network
```

```sh
python -m mcp_toolcall_lab.source_access request.json
```

The CLI emits JSON and returns 0 on success, 2 on operation/input/transport errors.
Argparse usage errors retain its usual stderr/exit-2 convention. Python callers
receive native exceptions; wrappers must preserve failures. Local paths only
belong to this CLI, not to remotely exposed registry operations.

`generic` extracts all outermost articles, otherwise mains, otherwise the document.
`github-readme` requires exactly one `article.markdown-body`; missing/ambiguous
markup fails explicitly. Profiles are local extraction rules, not a network allowlist.
Output includes normalized text, headings, HTTP(S) link occurrences, main/article
id/class observations, tag counts and stylesheet references. Scripts/styles/templates
are not text or executable output. Empty selected content and excessive nesting fail.
Links are listed but never followed, so a 31-item calendar needs no 31-page crawl.

This parser is not an HTML5 browser DOM, visual/CSS evaluator, PDF parser, or full
mirror. Link resolution uses the supplied source URL (HTML `<base>` is not honored).
Hidden CSS content is not visibility-filtered. Whitespace normalization is not a
pixel-faithful or preformatted-code export. Consumers must display extracted strings
as text, not executable markup. Pages cross-origin fetch is not proved by a local
urllib experiment; Python runtime delivery to Pages is outside this PR.

## Verification and next slice

```sh
PYTHONPATH=src python -m pytest tests/test_source_access.py experiments/html_snapshot/test_extract.py --junitxml=source-access.xml
```

Tests inject transport and block sockets: one GET followed by repeated extractions,
31 links without following them, preserved API cache reuse, selection isolation,
structure drift, hash mismatch, nesting/size/type/encoding/HTTP errors, and CLI
exit/snapshot preservation. Existing test discovery collects `tests/test_source_access.py`.
No extra browser job is necessary for these contracts. Original real GitHub capture
observations remain in `experiments/html_snapshot/README.md`; the experiment CLI
now delegates to the shared parser. No search-result live capture is claimed.

Next slices: provider search-result fixtures, additional API adapters using their
existing implementations, and selected MCP/UI exposure. Larger coverage should be
built from local fixtures before adding live requests; add browser tests only for
actual browser behavior. Measure requests and bytes rather than assuming HTML is
always smaller than an API response.

## Offline selectors and optional API adapter binding

`html/extract` accepts an optional `selector` with the generic profile. The
supported subset is tags, `#id`, `.class`, compounds, descendant whitespace and
child `>`. All matching outermost scopes are read in document order. Unsupported
syntax and zero matches fail explicitly; nested matches do not duplicate text.
This inspects saved markup only, never CSS computed styles or scripts. Selector
changes reuse the same snapshot; no linked resource is fetched.

Application code can pass the bundled trusted niconico Snapshot adapter to
`default_registry(niconico_adapter=adapter)`. It registers `niconico/search`,
delegates to the existing `paged_search`, and retains completion, pagination and
version evidence. Explicit network opt-in still applies. This binding does not
implement another HTTP client or add a new live MCP tool.
The offline integration regression invokes the real adapter with injected transport
and pacing, covering opt-in, completed results, version drift, and page limits.
The JSON CLI retains the default HTML/Wikipedia registry; arbitrary module names
and transport callables cannot be supplied in JSON.

BlueProbe's follow-up `HtmlSource` accepts this same offline extraction function
and maps it to its records/counts/unknown contract. The optional package wiring
is explicit; no unpinned runtime download or copied parser is introduced.

## Offline CSS inspection

`css/inspect` accepts CSS text. `html/extract` can opt in with `include_css=true`
and a `stylesheets` mapping of already-saved reference URLs to CSS text. Embedded
style elements and inline style attributes are inspected too; script/template/
noscript subtrees do not contribute active CSS sources. No asset, import, font or
URL is fetched. Stylesheet links and @import source remain explicitly unloaded.

The report retains declaration order and duplicates, raw values, !important,
custom properties, var() references and literal hex colors (3/4/6/8 digits).
An #id selector is not a color; strings and url() fragments are excluded from
hex-color extraction. Each stylesheet has a content SHA-256. HTML snapshot and
saved CSS identities are distinct, so changed CSS is not treated as the same
evidence just because the HTML is unchanged.

Nested @media/@supports/@layer/@container rules retain their contexts. Contexts
are not evaluated. Other at-rule bodies and malformed declarations remain
observable as unknown; structurally malformed CSS raises an error. Tag/id/class/
descendant/child selectors and :root can be associated with saved elements;
unsupported selectors have status=unsupported and matched=null. No-match is
distinct. Quotes, comments and balanced parentheses/brackets are recognized.
CSS escapes are retained, not normalized. Full CSS grammar, specificity,
inheritance, variable substitution, named-color normalization, computed styles,
visibility and layout are not implemented. Text is not silently removed based
on display:none or an unevaluated conditional rule.

Each CSS source is limited to 2 MiB, 128 sources and bounded nesting. Callers
provide saved CSS explicitly. JSON remains data, and supplied CSS is inspected,
never inserted into a page or executed by this operation.

web-ui PR #43/#44 provide the static HTML/CSS emitter and optional lightweight
script links; this reader complements that output surface. The regression
fixture preserves tokens.css/base.css/components.css at commit
365b33dc2c16cf1f90b91e88095cbb576f49cbd9 as test data only, not a runtime vendor.

## Minimal Pages UI direction

Open WebUI and LibreChat remain integration-test clients. A production Pages UI
can be a small static HTML/CSS/JS export, generated with existing vendored Markdown
and shared UI helpers where applicable. Those chat clients are not prerequisites
for using the page. Python runs generation/tests; client interaction must use a
browser-capable implementation, not assume a Python server exists on Pages.

The first useful UI should accept pasted HTML or a selected saved file, show the
extracted text/headings/links with source and snapshot time, and permit local
export. Provider/profile changes reuse loaded content. Lightweight JS, CSS and
GIF assets may improve presentation when useful; no large UI framework is required.
New client extraction must share fixture expectations with Python before claiming
equivalence. Keep logic tests in pytest/Node and browser tests for actual file
input, interaction, rendering and network behavior. This PR supplies the source
foundation, not a completed Pages UI.
