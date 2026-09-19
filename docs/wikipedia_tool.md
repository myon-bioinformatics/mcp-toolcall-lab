# `fetch_wikipedia_section`: the one MCP tool that is not a mock

Every other tool in `catalog.py` is deterministic and offline on purpose
(its own module docstring: "never calls a live API"). This one tool
calls Wikipedia's own action API for real, so the heading-pulldown +
section-extraction UX (already on the static Pages demo, against the
vendored fixture corpus) can also be tried against a genuine article.

## Why an MCP tool, not client-side JS

The static "Try it" demo on GitHub Pages (`stub_demo.js`) deliberately
has no live-fetch capability — it is meant to run with zero backend. A
tool that fetches an arbitrary external URL belongs in the MCP catalog,
where a real client (LibreChat, Open WebUI, the stub) makes an actual
network call through the MCP server process, not in a static page that
should keep working with no server at all.

## Why Wikipedia's own API, not HTML scraping

`vendor/markdown.py`'s `html_to_markdown()` is explicitly a "conservative"
converter (headings, paragraphs, bold/italic, links, images, simple
lists — no `<table>`, no citation/infobox handling). A real Wikipedia
article's raw HTML is full of exactly what it does not handle: infoboxes,
reference lists, navigation boxes. Feeding that through would produce
garbled sections, not a clean heading → body split.

Wikipedia's `action=query&prop=extracts&explaintext=1&exsectionformat=wiki`
API sidesteps this entirely: it returns plain text, already stripped of
all of that markup, with `== Heading ==`-style section markers. No HTML
or wikitext parsing happens in this repo — `_wiki_headings_to_atx()`
rewrites those markers to ATX (`## Heading`) and hands the result to
`mcp_toolcall_lab.markdown_lib.parse_sections()`, the exact same
heading-splitter every other corpus in this repo already goes through.
One heading-splitting implementation, not a second one for "real"
documents — this is markdown.py staying in active, real use, not a
reason to extend it.

## Contract

- `fetch_wikipedia_section(title)` (no `heading`, or a blank one): every
  section's title only — the pulldown list. Deliberately no body text,
  so listing options doesn't force a possibly-large body payload.
- `fetch_wikipedia_section(title, heading)`: that one section's title,
  its ATX form (`heading_markdown`, e.g. `"## Geography"` — the section's
  own level, not hardcoded), and its body — everything after that
  heading up to the next one at any level (`lookup_heading()` — exact
  match first, then fuzzy).
- No match for `heading`: an empty result. Same "valid call, no rows is
  not an error" contract as every other tool in `catalog.py`.
- No such article, or the fetch fails outright (network, timeout): raises
  `WikipediaFetchError`, which propagates as a real MCP tool error — this
  is the one tool where that can legitimately happen for reasons outside
  the lab's own control.

## Testing without live network

This repo's own dev sandbox cannot reach `en.wikipedia.org` (the agent
proxy denies the CONNECT — same story as `scripts/fetch_tiny_cpu_gguf.py`
and `huggingface.co`). `tests/test_wikipedia_tool.py` covers the parsing
and dispatch logic against a mocked `urllib.request.urlopen` returning a
realistic MediaWiki API response shape. One real-network test exists,
gated behind `MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1` (never on by default —
a plain `pytest -q` must never depend on Wikipedia being reachable), for
a human or a CI runner with real internet to opt into.
