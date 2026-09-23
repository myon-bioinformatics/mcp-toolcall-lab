"""Low-impact pixiv Encyclopedia article adapter.

Fetches one public article HTML page, converts it with the vendored markdown.py,
then reuses the lab's common Markdown section model. A small TTL/LRU cache keeps
heading changes from causing repeat requests. Default CI must use fixtures.
"""
from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .markdown_lib import Section, load_markdown, lookup_heading, parse_sections

PIXIV_ARTICLE = "https://dic.pixiv.net/a/{title}"
TIMEOUT = 10.0
USER_AGENT = "mcp-toolcall-lab/1.0 (+https://github.com/myon-bioinformatics/mcp-toolcall-lab; low-rate research adapter)"
FIXTURE_ENV = "MCP_TOOLCALL_LAB_PIXIV_FIXTURE"
CACHE_TTL_ENV = "MCP_TOOLCALL_LAB_PIXIV_CACHE_TTL"
CACHE_TTL_SECONDS = 600.0
CACHE_MAXSIZE = 8


class PixivDictionaryFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PixivDictionaryArticle:
    canonical_title: str
    markdown: str
    source_url: str

    def sections(self) -> list[Section]:
        return parse_sections(self.markdown)

    def headings(self) -> list[dict[str, Any]]:
        return [{"heading": s.title, "level": s.level} for s in self.sections()]

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "canonical_title": self.canonical_title,
            "markdown": self.markdown,
            "headings": self.headings(),
            "source_url": self.source_url,
        }


_CACHE: OrderedDict[str, tuple[float, PixivDictionaryArticle]] = OrderedDict()


def _ttl() -> float:
    try:
        return max(0.0, float(os.environ.get(CACHE_TTL_ENV, CACHE_TTL_SECONDS)))
    except ValueError:
        return CACHE_TTL_SECONDS


def reset_pixiv_dictionary_cache() -> None:
    _CACHE.clear()


def _article_html(title: str) -> tuple[str, str]:
    fixture = os.environ.get(FIXTURE_ENV, "").strip()
    url = PIXIV_ARTICLE.format(title=urllib.parse.quote(title.strip(), safe=""))
    if fixture:
        return Path(fixture).read_text(encoding="utf-8"), url
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), url
    except Exception as exc:
        raise PixivDictionaryFetchError(f"could not fetch pixiv Encyclopedia article {title!r}: {exc}") from exc


def _html_to_markdown(raw_html: str) -> str:
    md = load_markdown()
    if md is None or not hasattr(md, "html_to_markdown"):
        raise PixivDictionaryFetchError("vendor/markdown.py with html_to_markdown() is required")
    converted = str(md.html_to_markdown(raw_html)).strip()
    if not converted:
        raise PixivDictionaryFetchError("pixiv Encyclopedia HTML produced no Markdown")
    return converted


def _canonical_title(markdown: str, requested: str) -> str:
    sections = parse_sections(markdown)
    for section in sections:
        if section.level == 1 and section.title.strip():
            return section.title.strip()
    return requested.strip()


def load_pixiv_dictionary_article(title: str) -> tuple[PixivDictionaryArticle, str]:
    cleaned = title.strip()
    if not cleaned:
        raise PixivDictionaryFetchError("article title is required")
    key = cleaned.casefold()
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and cached[0] > now:
        _CACHE.move_to_end(key)
        return cached[1], "hit"
    raw_html, url = _article_html(cleaned)
    markdown = _html_to_markdown(raw_html)
    article = PixivDictionaryArticle(_canonical_title(markdown, cleaned), markdown, url)
    _CACHE[key] = (now + _ttl(), article)
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_MAXSIZE:
        _CACHE.popitem(last=False)
    return article, "miss"


def fetch_pixiv_dictionary_article(title: str) -> dict[str, Any]:
    article, cache = load_pixiv_dictionary_article(title)
    result = article.as_tool_result()
    result["cache"] = cache
    return result


def fetch_pixiv_dictionary_section(title: str, heading: str = "") -> list[dict[str, str]]:
    article, _ = load_pixiv_dictionary_article(title)
    sections = article.sections()
    if not heading.strip():
        return [{"heading": s.title, "level": str(s.level)} for s in sections]
    section = lookup_heading(heading, sections, fuzzy=True)
    if section is None:
        return []
    # parse_sections() intentionally gives parent sections their nested children.
    # A direct child-heading selection should expose only that heading's own prose,
    # stopping before the next heading of any level (Wikipedia-style selector UX).
    lines = section.body.splitlines()
    own_body: list[str] = []
    for line in lines:
        if line.lstrip().startswith("#"):
            break
        own_body.append(line)
    return [{"heading": section.title, "level": str(section.level), "body": "\n".join(own_body).strip()}]
