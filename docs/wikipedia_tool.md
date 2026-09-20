# Wikipedia article + section tools (the ones that are not mocks)

Every other tool in `catalog.py` is deterministic and offline on purpose
(its own module docstring: "never calls a live API"). These two tools
call Wikipedia's own action API so a title → full article → heading
pulldown → section body UX can be tried against a genuine article, not
only the vendored fixture corpus.

`fetch_wikipedia_section` keeps its original list-of-rows contract.
`fetch_wikipedia_article` is the new structured result: the same
plaintext extract, the canonical title after redirects, and the heading
list derived from that extract.

## Two fetch paths: Pages `#wiki` vs MCP / local `/wiki`

The MediaWiki Action API supports CORS via `origin=*`, so a static page
on github.io **can** `fetch` `https://{lang}.wikipedia.org/w/api.php`
from the browser. That is what published `#wiki` does. It is **not** an
MCP tool call.

| Surface | Who fetches | How |
| --- | --- | --- |
| GitHub Pages `#wiki` | the browser | `fetch` MediaWiki Action API with `origin=*` (no MCP, no Docker) |
| Deno Wikipedia MCP (`POST /mcp`) | the MCP server process | `deploy/wikipedia-mcp` Streamable HTTP (keyless, Deno KV sessions, `lang` restricted to MediaWiki codes). Not wired to Pages yet. See `docs/wikipedia_mcp_deploy.md`. |
| Local `GET /wiki` + FastMCP `fetch_wikipedia_*` | the stub / Python MCP server process | `wikipedia_tool.py` (unchanged) |

A tool that LibreChat / Open WebUI / the stub should call still belongs
in the MCP catalog, where the **server process** makes the HTTP call.
GitHub Pages remains a static host: it cannot keep that backend or a
github.io `/wiki` route (that path stays 404). The published index stays
`index.html`; `#wiki` (optional `?view=wiki`) hide/shows the browser
MediaWiki form. Heading switches reuse the in-memory extract (same idea
as the Python process cache). The mock `stub_demo.js` heading pulldown
is still not published.

## Why Wikipedia's own API, not HTML scraping

`vendor/markdown.py`'s `html_to_markdown()` is a conservative converter
(headings, paragraphs, bold/italic/strikethrough, links, images, simple
lists, simple `<table>`, checkbox `<li>`, heading/p attributes). A real
Wikipedia article's raw HTML is still full of what it does not handle:
infoboxes, citation/reference lists, navigation boxes. Feeding that
through would hide those limits behind a garbled conversion. The lab
does not own a second HTML→Markdown path.

Wikipedia's `action=query&prop=extracts&explaintext=1&exsectionformat=wiki`
API sidesteps this: it returns plain text, already stripped of that
markup, with `== Heading ==`-style section markers. The stub displays
that extract HTML-escaped. `_wiki_headings_to_atx()` rewrites the
markers to ATX (`## Heading`) only to build the heading list / section
split via `mcp_toolcall_lab.markdown_lib.parse_sections()`. One
heading-splitter, not a second parser for "real" documents, and not a
claim that markdown.py can ingest Wikipedia HTML.

## Contract

### `fetch_wikipedia_article(title, lang="en")`

One object:

```json
{
  "canonical_title": "Yokohama",
  "lang": "en",
  "extract": "…MediaWiki plaintext, including == Heading == markers…",
  "headings": [
    {"heading": "Yokohama", "level": 1},
    {"heading": "Geography", "level": 2}
  ]
}
```

The extract is the API plaintext, not ATX and not HTML. Headings come
from the same fetch. No extra network call is required to list headings
or to later take a section.

### `fetch_wikipedia_section(title, heading="")`

Unchanged row shape:

- no `heading` (or a blank one): every section's title only — the pulldown
  list, deliberately without body text.
- a matching `heading`: that section's title, its ATX form
  (`heading_markdown`, e.g. `"## Geography"` — the section's own level),
  and its body. The body runs until the next heading at the same level or
  shallower, so a heading with no prose of its own — only deeper
  subsections (e.g. an empty parent followed straight by its `###`
  children) — still returns those nested subsections instead of an empty
  string. Same rule as `markdown_lib.parse_sections()`'s other callers and
  the Pages `#wiki` JS's `parseWikiSections`.
- no match: an empty result. Same "valid call, no rows is not an error"
  contract as every other tool in `catalog.py`.
- no such article, or the fetch fails outright: raises
  `WikipediaFetchError`, which propagates as a real MCP tool error.

Listing headings and then fetching one body for the same title+language
is one HTTP call: the process cache sits under both tools.

## In-process cache

`wikipedia_tool.py` keeps a thread-safe TTL/LRU cache inside the MCP
server process (and inside the stub process when `/wiki` runs
in-process):

- default TTL 5 minutes (`MCP_TOOLCALL_LAB_WIKI_CACHE_TTL`)
- default size 16 canonical articles (`MCP_TOOLCALL_LAB_WIKI_CACHE_MAXSIZE`)
- a MediaWiki redirect stores the requested title and the canonical
  title on the **same** entry; heading switches reuse it
- the cache lock is **not** held during loader/HTTP; same-title misses
  single-flight, and a hit on a warm title is not blocked by another
  title's in-flight fetch (the stub is a `ThreadingHTTPServer`)
- cache hit/miss, TTL expiry, the size cap, and that lock/load split
  are unit-tested in `tests/test_wikipedia_cache.py`

The copy-paste MCP mocks (`openwebui_mcp_mock.py`, `librechat_mcp_mock.py`)
inline this module (and the rest of the FastMCP server, including #19's
protocol-event JSONL and UI `message_id` harvest). After changing the
package, regenerate both with `python -m mcp_toolcall_lab.export` rather
than editing the copies by hand.

## Stdlib stub UI (`GET /wiki`)

Thin HTML form, GET so the URL is the state (Playwright CLI can
screenshot it without filling widgets):

- `/wiki?title=Yokohama` — title entered, full plaintext extract, heading
  dropdown
- `/wiki?title=Yokohama&heading=Geography` — the same article from cache,
  selected section body

The heading `<option>` labels carry the same `## `/`### ` ATX prefix as
the Pages `#wiki` JS (the option's `value` stays the bare title, so
`heading=` query params are unaffected).

Displayed text is `html.escape`d into `<pre>`. GitHub Pages does not
host this **server** form (a `/wiki` path on github.io stays 404). Local
`/wiki` may include a small authored `<style>` block; the published
Pages `#wiki` panel is a different client: vanilla JS, no authored CSS,
browser → MediaWiki CORS. `#wiki` is still on `index.html`, not a
second Pages route.

```bash
python -m mcp_toolcall_lab.stub_front serve --port 8765
# open http://127.0.0.1:8765/wiki
# Pages browser MediaWiki UI: https://myon-bioinformatics.github.io/mcp-toolcall-lab/#wiki
```

## Testing without live network

This repo's own dev sandbox cannot reach `en.wikipedia.org` (the agent
proxy denies the CONNECT — same story as `scripts/fetch_tiny_cpu_gguf.py`
and `huggingface.co`). Default `pytest -q` never depends on Wikipedia:

- `tests/test_wikipedia_tool.py` / `tests/test_wikipedia_cache.py` mock
  `urllib.request.urlopen` with a MediaWiki API response shape
- `MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE` points at
  `fixtures/wikipedia/yokohama_extract.json` for deterministic CI
  screenshots
- one real-network test remains gated behind
  `MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1`

Playwright CLI screenshots are **not** part of `pytest -q`. They live in
the dedicated workflow `.github/workflows/wikipedia-article-screenshot.yml`
(`workflow_dispatch` + path-filtered pull_request) and the local script:

```bash
pip install -e '.[browser-test]'
playwright install chromium
python scripts/wikipedia_article_screenshot.py --out test-results/wiki-screenshots
# live Wikipedia, explicit:
MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1 python scripts/wikipedia_article_screenshot.py --live
```

The script invokes `python -m playwright screenshot --full-page` against
the two GET URLs above and writes `wiki-title-entered.png` plus
`wiki-heading-selected.png`.
