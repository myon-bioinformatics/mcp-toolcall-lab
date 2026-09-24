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
