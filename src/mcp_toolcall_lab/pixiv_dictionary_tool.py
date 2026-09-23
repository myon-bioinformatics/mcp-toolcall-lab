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
from urllib.error import HTTPError

from .markdown_lib import Section, load_markdown, lookup_heading, parse_sections

PIXIV_ARTICLE = "https://dic.pixiv.net/a/{title}"
TIMEOUT = 10.0
USER_AGENT = "mcp-toolcall-lab/1.0 (+https://github.com/myon-bioinformatics/mcp-toolcall-lab; low-rate research adapter)"
FIXTURE_ENV = "MCP_TOOLCALL_LAB_PIXIV_FIXTURE"
CACHE_TTL_ENV = "MCP_TOOLCALL_LAB_PIXIV_CACHE_TTL"
CACHE_TTL_SECONDS = 600.0
CACHE_MAXSIZE = 8


class PixivDictionaryFetchError(RuntimeError):
    """Raised when an article cannot be fetched, converted, or was requested with a bad title.

    ``stage`` tells callers which layer failed (``input`` / ``upstream_http`` /
    ``upstream_network`` / ``convert``) so an HTTP front end can map it to a
    status code without re-deriving it from the message text. ``upstream_status``
    carries the origin HTTP status when ``stage`` is ``upstream_http``.
    """

    def __init__(self, message: str, *, stage: str, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.upstream_status = upstream_status


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
    except HTTPError as exc:
        raise PixivDictionaryFetchError(
            f"pixiv Encyclopedia article {title!r} returned HTTP {exc.code}",
            stage="upstream_http",
            upstream_status=exc.code,
        ) from exc
    except Exception as exc:
        raise PixivDictionaryFetchError(
            f"could not fetch pixiv Encyclopedia article {title!r}: {exc}",
            stage="upstream_network",
        ) from exc


def _html_to_markdown(raw_html: str) -> str:
    md = load_markdown()
    if md is None or not hasattr(md, "html_to_markdown"):
        raise PixivDictionaryFetchError("vendor/markdown.py with html_to_markdown() is required", stage="convert")
    try:
        converted = str(md.html_to_markdown(raw_html)).strip()
    except Exception as exc:
        raise PixivDictionaryFetchError(f"failed to convert pixiv Encyclopedia HTML: {exc}", stage="convert") from exc
    if not converted:
        raise PixivDictionaryFetchError("pixiv Encyclopedia HTML produced no Markdown", stage="convert")
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
        raise PixivDictionaryFetchError("article title is required", stage="input")
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
    # parse_sections() may include nested descendants in a parent body. For a
    # selected heading, recover the exact heading from the normalized Markdown
    # and return only its own prose up to the next heading of any level.
    lines = article.markdown.splitlines()
    target = section.title.casefold()
    start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            title_text = stripped.lstrip("#").strip().rstrip("#").strip()
            if title_text.casefold() == target:
                start = index + 1
                break
    if start is None:
        body = section.body
    else:
        own_body: list[str] = []
        for line in lines[start:]:
            if line.lstrip().startswith("#"):
                break
            own_body.append(line)
        body = "\n".join(own_body).strip()
    return [{"heading": section.title, "level": str(section.level), "body": body}]
