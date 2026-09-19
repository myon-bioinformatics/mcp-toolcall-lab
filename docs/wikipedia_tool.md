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

## Why an MCP tool, not client-side JS

A tool that fetches an arbitrary external URL belongs in the MCP catalog,
where a real client (LibreChat, Open WebUI, the stub) makes an actual
network call through the MCP server process. GitHub Pages is a static
host: it cannot fetch Wikipedia. The published report is generation
identity + induction to local `/wiki`, not a mock heading pulldown.

The stdlib stub serves a **local** `GET /wiki` form (title input +
server-rendered heading `<select>`). That form is not published as a live
backend on Pages. The Pages report says so explicitly.

## Why Wikipedia's own API, not HTML scraping

`vendor/markdown.py`'s `html_to_markdown()` is explicitly a "conservative"
converter (headings, paragraphs, bold/italic, links, images, simple
lists — no `<table>`, no citation/infobox handling). A real Wikipedia
article's raw HTML is full of exactly what it does not handle: infoboxes,
reference lists, navigation boxes. Feeding that through would hide those
limits behind a garbled conversion.

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
  and its body.
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

Displayed text is `html.escape`d into `<pre>`. GitHub Pages does not
host this form. Local `/wiki` may include a small authored `<style>`
block for the form; the published Pages report is a different host
(no Wikipedia-form CSS, and no live Wikipedia backend) on purpose.

```bash
python -m mcp_toolcall_lab.stub_front serve --port 8765
# open http://127.0.0.1:8765/wiki
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
