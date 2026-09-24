"""Pixiv Encyclopedia real-source extraction (parser/fixture phase, #60).

Follow-up to #59's History -> Source -> Extract route. Parses the HTML shape
of a Pixiv Encyclopedia source (title, reading, overview, section headings,
plus two best-effort labeled fields) into a stable structured extract, using
the *same* vendored ``markdown.py`` heading/section model
``pixiv_dictionary_tool.py`` already relies on -- via ``markdown_lib``'s
``load_markdown()`` / ``parse_sections()`` / ``own_body()`` -- rather than a
second parser.

``parent_article`` and ``english_title`` are recognized only from an
explicit labeled line (``"English: ..."`` / ``"英語表記：..."`` /
``"Parent article: ..."`` / ``"親記事：..."``). No positional or markup
heuristic backs these two fields: this repo has no confirmed observed markup
for them from a real ``dic.pixiv.net`` revision-source page (see
``docs/pixiv_source_fixture_contract.md``), so an absent label simply leaves
the field empty instead of guessing unsupported Pixiv syntax. ``body``
always carries the full converted Markdown verbatim, so content that this
module does not recognize as a named field is still preserved rather than
silently dropped.

Not wired into ``fetch_pixiv_dictionary_article``/``_section`` yet -- that is
the next PR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .markdown_lib import load_markdown, own_body, parse_sections

_READING_RE = re.compile(r"^(.*?)[(（]([^()（）]+)[)）]\s*$")
_ENGLISH_LABEL_RE = re.compile(r"^(?:english\s*title|english|英語表記)\s*[:：]\s*(.+)$", re.IGNORECASE)
_PARENT_LABEL_RE = re.compile(r"^(?:parent\s*article|parent|親記事)\s*[:：]\s*(.+)$", re.IGNORECASE)


class PixivSourceExtractError(RuntimeError):
    """Raised when the source HTML cannot be converted to usable Markdown."""


@dataclass(frozen=True)
class PixivSourceExtract:
    title: str
    reading: str
    overview: str
    parent_article: str
    english_title: str
    headings: list[dict[str, Any]]
    body: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "reading": self.reading,
            "overview": self.overview,
            "parent_article": self.parent_article,
            "english_title": self.english_title,
            "headings": self.headings,
            "body": self.body,
        }


def _split_reading(line: str) -> tuple[str, str]:
    cleaned = line.strip()
    match = _READING_RE.match(cleaned)
    if not match:
        return cleaned, ""
    title, reading = match.group(1).strip(), match.group(2).strip()
    if not title or not reading:
        return cleaned, ""
    return title, reading


def _labeled_field(markdown: str, pattern: re.Pattern[str]) -> str:
    for line in markdown.splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def html_to_pixiv_source_markdown(raw_html: str) -> str:
    md = load_markdown()
    if md is None or not hasattr(md, "html_to_markdown"):
        raise PixivSourceExtractError("vendor/markdown.py with html_to_markdown() is required")
    try:
        converted = str(md.html_to_markdown(raw_html)).strip()
    except Exception as exc:
        raise PixivSourceExtractError(f"failed to convert pixiv Encyclopedia source HTML: {exc}") from exc
    if not converted:
        raise PixivSourceExtractError("pixiv Encyclopedia source HTML produced no Markdown")
    return converted


def extract_pixiv_source(raw_html: str, *, requested_title: str = "") -> PixivSourceExtract:
    markdown = html_to_pixiv_source_markdown(raw_html)
    sections = parse_sections(markdown)
    title_section = next((s for s in sections if s.level == 1 and s.title.strip()), None)

    if title_section is not None:
        title, reading = _split_reading(title_section.title)
        overview = own_body(markdown, title_section)
    else:
        title, reading = requested_title.strip(), ""
        overview = ""

    headings = [{"heading": s.title, "level": s.level} for s in sections]
    english_title = _labeled_field(markdown, _ENGLISH_LABEL_RE)
    parent_article = _labeled_field(markdown, _PARENT_LABEL_RE)

    return PixivSourceExtract(
        title=title,
        reading=reading,
        overview=overview,
        parent_article=parent_article,
        english_title=english_title,
        headings=headings,
        body=markdown,
    )


def fetch_pixiv_source_extract(raw_html: str, *, requested_title: str = "") -> dict[str, Any]:
    return extract_pixiv_source(raw_html, requested_title=requested_title).as_dict()
