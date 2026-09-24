"""Conservative normalization of observed Pixiv Encyclopedia source markup.

Follow-up to #60/#61 (see ``docs/pixiv_markup_normalization.md``). A real
BLEACH history/source sample was manually observed; only its *structural*
syntax -- never article prose -- is reproduced anywhere in this repo.

This module does **not** implement a Pixiv renderer. It reuses vendor
``markdown.py``'s own link/image builders (``make_link``/``make_image``) and
its own conversion rules wherever Pixiv's syntax already degrades safely
through them (e.g. an unmatched leading ``*`` marker, or a pipe row with no
``| --- |`` delimiter). A thin normalization layer is added only for the
handful of Pixiv-specific tokens vendor ``markdown.py`` has no notion of at
all: ``[[label]]`` / ``[[label>anchor]]`` wiki links, ``[pixivimage:ID]`` /
``[pixivimage:ID:size]`` embeds, unspaced ``-[[label]]`` list lines, and
``NEXT▶︎[[label]]`` navigation lines. Everything else -- including malformed
or unrecognized tokens -- passes through unchanged; see the SUPPORTED /
UNSUPPORTED sections of ``docs/pixiv_markup_normalization.md`` for the full
contract.

Not wired into ``pixiv_source_extract.py`` or the MCP fetch tools yet --
that is a later phase.
"""

from __future__ import annotations

import re

from .markdown_lib import load_markdown, parse_sections

_LIST_DASH_RE = re.compile(r"^([ \t]*)-(?=\[\[)", re.MULTILINE)
_NEXT_ARROW_RE = re.compile(r"NEXT▶︎(?=\[\[)")
_PIXIV_HEADING_RE = re.compile(r"^(\*{1,6})(【[^\n】]+】(?:[／/][^\n]+)?)\s*$", re.MULTILINE)
_PIXIVIMAGE_RE = re.compile(r"\[pixivimage:(\d+)(?::([A-Za-z0-9]+))?\]")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]>]+)(?:>([^\[\]]+))?\]\]")


class PixivMarkupNormalizeError(RuntimeError):
    """Raised when vendor ``markdown.py``'s link/image builders are unavailable."""


def _escape_link_url(url: str) -> str:
    """Percent-encode chars vendor markdown.py's own link regex can't hold.

    ``_INLINE_LINK_RE`` in vendor ``markdown.py`` matches a link URL with
    ``[^)\\s]+`` -- literal ``)`` or whitespace there would truncate the URL
    early on re-parse. The observed ``[[死神>死神(BLEACH)]]`` anchor contains
    exactly that, so this is the "thin Pixiv-specific layer" the docs call
    for: markdown.py can't interpret a paren-bearing URL, so we escape it
    before handing the URL to ``make_link``.
    """
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def normalize_pixiv_markup(markdown: str) -> str:
    """Conservatively normalize observed Pixiv source tokens in ``markdown``.

    Idempotent: every substitution below produces text that no longer
    matches its own pattern (wiki links lose their ``[[``/``]]``, pixivimage
    tokens lose their ``[pixivimage:`` prefix, the inserted list/NEXT
    spacing breaks the no-space lookahead that triggered it), so calling
    this twice is the same as calling it once.
    """
    md = load_markdown()
    if md is None or not hasattr(md, "make_link") or not hasattr(md, "make_image"):
        raise PixivMarkupNormalizeError("vendor/markdown.py with make_link()/make_image() is required")

    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _PIXIV_HEADING_RE.sub(lambda m: ("#" * len(m.group(1))) + " " + m.group(2), text)

    text = _LIST_DASH_RE.sub(r"\1- ", text)
    text = _NEXT_ARROW_RE.sub("NEXT ▶︎ ", text)

    def _pixivimage_repl(match: re.Match[str]) -> str:
        image_id, size = match.group(1), match.group(2)
        return md.make_image(f"pixiv image {image_id}", f"pixivimage:{image_id}", size)

    text = _PIXIVIMAGE_RE.sub(_pixivimage_repl, text)

    def _wikilink_repl(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        anchor = (match.group(2) or label).strip()
        return md.make_link(label, _escape_link_url(anchor))

    text = _WIKILINK_RE.sub(_wikilink_repl, text)

    return text


def pixiv_to_markdown(source: str) -> str:
    """Convert observed Pixiv source syntax to Markdown consumable by markdown.py."""
    return normalize_pixiv_markup(source)


def pixiv_sections(source: str):
    """Normalize Pixiv source then expose the shared markdown.py-backed sections."""
    return parse_sections(pixiv_to_markdown(source))
