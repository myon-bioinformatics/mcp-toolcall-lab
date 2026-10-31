# Character-budget compression (`char_compress`)

`src/mcp_toolcall_lab/char_compress.py` shrinks English text for outputs with a
character limit (for example an X post) while keeping as much information as
possible. It is stdlib-only; `tiktoken` (o200k_base) is optional and only used to
record token counts and to guard against token-increasing rules.

```bash
uv run python -m mcp_toolcall_lab.char_compress --limit 140 "We need at least 3 reviewers..."
uv run python -m mcp_toolcall_lab.char_compress --mode chars < post.txt
```

The CLI prints JSON (`text`, `fits`, `over_by`, `applied`, `risky`, `before`/`after`
metrics: `chars`, `utf8_bytes`, `tokens`) and exits 1 when the text still does not fit.

## Rules and order

Rules run in order, one at a time, and budget mode stops as soon as the text fits.
**Nothing is ever truncated**: if all rules are used and it is still too long,
`fits=False` and `over_by` report the gap.

| class | rules | notes |
| --- | --- | --- |
| lossless | strip VS16 (U+FE0F), collapse spaces, no space before punctuation, `...`→`…`, `do not`→`don't` and other standard contractions | `it is`→`it's` only before another word. Stripping VS16 can change emoji presentation (e.g. ❤️→❤ may render text-style). |
| symbol | `at least N`→`≥N`, `at most N`→`≤N`, `more/greater than N`→`>N`, `less/fewer than N`→`<N`, `N percent`→`N%` | Only before a number, so “at least he tried” is untouched. |
| abbrev (reported in `risky`) | ` and `→` & `, `versus`→`vs`, `for example`→`e.g.`, `information`→`info`, `without`→`w/o`, `with`→`w/`, `because`→`b/c` | Readable, but the original wording is not always recoverable. |
| chars-only | `approximately `→`≈` | o200k gives no token win in context (` ≈4` = ` approximately 4` = 3 tokens), so it runs only with `mode="chars"`. |

In `mode="tokens"` (default) a rule is also skipped whenever it would raise the
o200k token count (e.g. ` with`→` w/` and ` because`→` b/c` cost +1 token each).

## Restoring the original (AI readability) — 未検証

No LLM ran in this change; these are reasoned expectations.

- lossless/symbol rules: an AI should restore the meaning reliably. `≥3`→“at least 3”
  is exact; `>5` may come back as “more than” or “greater than” (meaning kept, wording not).
- contractions: meaning kept; whether the original was contracted is lost.
- `&`, `vs`, `e.g.`, `info`, `w/`, `w/o`: common and readable; wording may differ.
- `b/c`: usually “because”, but can be read as “b/c” literally in code-ish text — risky.
- `≈`: readable as “approximately/about”; exact wording is lost.
- VS16 strip: no meaning change.

## Verification

- `uv run --with tiktoken --extra test pytest -q tests/test_char_compress.py`
- Mutation test against the real source (restores the file afterwards):
  `uv run --with tiktoken --extra test python scripts/mutate_char_compress.py`
