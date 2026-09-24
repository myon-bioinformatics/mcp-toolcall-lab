# Pixiv real-source fixture and extraction contract

This PR follows #59. It turns the verified Pixiv Encyclopedia history/source route into a small, testable extraction contract before wiring it into the MCP tool.

## Scope

1. Add 2–3 **minimal, synthetic/redacted fixtures derived from observed Pixiv source structure**, with provenance metadata. Do not copy whole Pixiv articles.
2. Include structurally different examples, including Japanese and at least one non-Japanese/alternate-title case where practical.
3. Record the observed source fields/markup needed for title, reading, summary/overview, parent article, English title, and section headings. Do not guess unsupported Pixiv syntax.
4. Strengthen Pages/source extraction using existing helpers from the vendored `markdown.py` where they genuinely fit (heading/section/text helpers); avoid duplicate parsers and new dependencies.
5. Add parser/extraction regression tests and round-trip/stability tests where round-trip is meaningful. Preserve unknown content rather than silently dropping it.
6. Keep CI deterministic and offline: no mandatory live dic.pixiv.net request.
7. Keep the real upstream URL/history/source URL contract from #59 covered.
8. Do not yet merge this into `fetch_pixiv_dictionary_article/section`; that is the next PR.

## Fixture policy

BLEACH may be used as provenance/shape evidence, but fixtures must contain only the minimum text needed to prove structure. Prefer synthetic replacement prose for article body content while retaining the structural markers being tested. Document which parts are synthetic and which observed field names/shape motivated them.

## Acceptance

- Existing tests remain green.
- New fixture-driven tests cover valid extraction and malformed/unknown input.
- Unicode, Japanese text, whitespace/line endings, empty fields, unknown fields, and duplicate/repeated section-like structures receive explicit coverage where relevant.
- No browser/live-network dependency is introduced.
- `markdown.py` reuse is documented; if a helper cannot safely be reused, explain why in code/tests/docs.
- Run the repository's normal test lanes and report exact head CI.

This PR is intentionally the parser/fixture phase. MCP tool integration follows separately.
