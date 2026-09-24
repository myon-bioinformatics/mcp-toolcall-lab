# Pixiv source normalization contract

This phase follows #60 and precedes MCP integration.

A real Pixiv Encyclopedia BLEACH history/source sample was manually observed. Use it as structural evidence only; do not store the full article text.

## Goal

Normalize only the observed Pixiv syntax that can be represented safely through the existing vendored markdown.py path. Do not attempt a full Pixiv renderer.

Observed structural forms include:

- `*【INFORMATION】／作品情報`
- `**【OVERVIEW】／概要`
- `[[死神>死神(BLEACH)]]`
- `[pixivimage:61456650]`
- `|^作者|[[久保帯人]]|`
- `-[[黒崎一護]]`
- `NEXT▶︎[[獄頤鳴鳴篇]]`

Also cover emphasis combinations, blank-line runs, duplicate/nested headings, malformed tokens, Unicode/Japanese, and image size suffixes such as `:ms`.

## Rules

Prefer markdown.py helpers and conversion behavior. Add only a thin Pixiv-specific normalization layer where markdown.py cannot directly interpret the syntax. Preserve unknown syntax losslessly where practical and never invent unsupported semantics.

Fixtures must be minimal synthetic/redacted reproductions of structural tokens, not copied article prose. Document SUPPORTED / UNSUPPORTED behavior explicitly. Add regression and normalization-stability/idempotence tests. CI stays offline/deterministic.

Do not wire this into `fetch_pixiv_dictionary_article` or `fetch_pixiv_dictionary_section` yet; MCP integration is the next phase.

## Implementation

`src/mcp_toolcall_lab/pixiv_markup_normalize.py` keeps two boundaries: `normalize_pixiv_markup()` performs conservative token-level normalization, while `pixiv_to_markdown()` promotes only the observed Pixiv star-heading form using vendor `markdown.py`'s `heading()` builder. `pixiv_sections()` then delegates the converted text to the shared `parse_sections()` path. Link/image normalization likewise reuses vendor `make_link()` / `make_image()` rather than hand-formatting Markdown. Fixtures live in `fixtures/pixiv_dictionary/markup_normalization_cases.json` (input/expected pairs, each tagged with the observed token category); tests are in `tests/test_pixiv_markup_normalize.py`, covering regression, idempotence/stability, and the error path when vendor `markdown.py` is unavailable.

Not yet wired into `pixiv_source_extract.py`'s `body`/`extract_pixiv_source()` pipeline or the MCP fetch tools -- this PR is the normalization-layer phase only.

### SUPPORTED

- Observed Pixiv line-leading `*{1,6}【LABEL】／title` (and `/` separator variant) is promoted to the equivalent ATX heading level before the shared `markdown.py` section parser runs. Other star-prefixed lines and ordinary emphasis remain untouched.

- `[[label>anchor]]` and `[[label]]` wiki links → Markdown links via `make_link()`. A `>`-less link uses the label itself as the target. Anchors containing `(`, `)`, or a space are percent-encoded (`%28`/`%29`/`%20`) because vendor `markdown.py`'s own `_INLINE_LINK_RE` matches a URL with `[^)\s]+` -- a literal `)` there would truncate the link on re-parse. This is exactly the case for the observed `[[死神>死神(BLEACH)]]` anchor.
- `[pixivimage:ID]` and `[pixivimage:ID:size]` → Markdown images via `make_image()`, using a `pixivimage:ID` pseudo-URL (no real CDN URL is known at this layer) and the size suffix (e.g. `ms`) as the image title when present.
- Unspaced `-[[label]]` list lines → a space is inserted after the `-` so vendor `markdown.py`'s own `_LIST_ITEM_RE` (which requires `\s+` after the marker) recognizes it as a list item; the link then normalizes as above. Already-spaced `- [[label]]` lines are a no-op for this step.
- `NEXT▶︎[[label]]` navigation lines → spacing is inserted around `▶︎` so the same link normalization applies; the `NEXT ▶︎` text itself is preserved verbatim, never translated or dropped.
- `\r\n` / `\r` line endings are normalized to `\n` (a literal replacement, not a `splitlines()`/`join()` round-trip, so line count and trailing-newline presence are otherwise preserved exactly).
- A recognized token (link/image) still normalizes even when it sits inside an otherwise-unsupported line, e.g. the `[[...]]` inside a Pixiv `|^label|...|` table row.
- Standard Markdown already understood natively by vendor `markdown.py` (matched-pair `*em*`/`**strong**`, blank-line runs, `#` ATX headings) is left completely untouched -- this layer performs no action on it.

### UNSUPPORTED (preserved verbatim, not guessed)

- Pixiv's `|^label|value|` table syntax is **not** rendered as a table. Vendor `markdown.py`'s `_is_table_start` requires a `| --- |` delimiter row after the header, which this single-line Pixiv form never has, so it safely degrades to a plain paragraph. No delimiter row is synthesized.
- Malformed/truncated tokens -- unterminated `[[label`, empty `[[]]`, `[pixivimage:]` with no id, or a non-numeric `[pixivimage:abc]` id -- do not match the conversion patterns and pass through unchanged rather than being guessed.
- Duplicate/nested headings receive no dedup or renumbering; that stays vendor `markdown.py`'s own heading/section handling.
