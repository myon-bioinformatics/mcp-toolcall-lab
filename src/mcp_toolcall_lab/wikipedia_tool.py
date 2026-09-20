"""Fetch a real Wikipedia article's plaintext extract over HTTP.

This is the one tool family in this lab's MCP catalog that is **not** a
deterministic offline mock -- every other tool in ``catalog.py`` returns
canned data on purpose (see its own module docstring: "never calls a
live API"). These helpers call Wikipedia's own action API for real, so a
title → full article → heading-pulldown → section-body UX can be tried
against a genuine article, not only the vendored ``fixtures/stub_front/*.md``
corpus.

Wikipedia's ``action=query&prop=extracts&explaintext=1&exsectionformat=wiki``
does the heavy lifting: it returns already-plain-text article content
(infoboxes, references, navboxes, and other markup already stripped by
Wikipedia's own extract engine) with MediaWiki ``== Heading ==`` markers.
No HTML scraping happens here, and ``markdown.py`` is never asked to
re-convert Wikipedia HTML -- those limits stay visible. The markers are
rewritten to ATX (``## Heading``) only to build a heading list / section
split via ``mcp_toolcall_lab.markdown_lib.parse_sections()``, the same
heading-splitter every other corpus in this repo already goes through.

The MCP server process keeps a small thread-safe TTL/LRU cache of
extracts keyed by language + title. Heading switches for the same
article reuse that entry. A MediaWiki redirect stores the requested
title and the canonical title on the same entry.

This sandbox's own dev environment cannot reach en.wikipedia.org (the
agent proxy denies the CONNECT, same as huggingface.co -- see
scripts/fetch_tiny_cpu_gguf.py's module docstring for that precedent),
so the live path is covered by unit tests against a mocked
``urllib.request.urlopen`` here, plus a JSON fixture file
(``MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE``) for deterministic CI, plus one
real-network integration test gated behind
``MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1`` -- never on by default, so a plain
``pytest -q`` never depends on Wikipedia being reachable.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from .markdown_lib import Section, lookup_heading, parse_sections

WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
DEFAULT_LANG = "en"
TIMEOUT = 10.0
USER_AGENT = "mcp-toolcall-lab/1.0 (https://github.com/myon-bioinformatics/mcp-toolcall-lab; lab demo, not production traffic)"
FIXTURE_ENV = "MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE"
CACHE_TTL_SECONDS = 300.0
CACHE_MAXSIZE = 16
CACHE_TTL_ENV = "MCP_TOOLCALL_LAB_WIKI_CACHE_TTL"
CACHE_MAXSIZE_ENV = "MCP_TOOLCALL_LAB_WIKI_CACHE_MAXSIZE"

_WIKI_HEADING_RE = re.compile(r"^(=+)\s*(.+?)\s*=+\s*$", re.MULTILINE)

CacheLookup = Literal["hit", "miss"]


class WikipediaFetchError(RuntimeError):
    """The live fetch failed: network, timeout, no such article, or a bad response."""


def _norm_title(title: str) -> str:
    return title.strip().casefold()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class WikipediaArticle:
    """One MediaWiki plaintext extract plus the heading list derived from it."""

    canonical_title: str
    lang: str
    extract: str

    def sections(self) -> list[Section]:
        atx = f"# {self.canonical_title}\n\n{_wiki_headings_to_atx(self.extract)}"
        return parse_sections(atx)

    def headings(self) -> list[dict[str, Any]]:
        return [{"heading": section.title, "level": section.level} for section in self.sections()]

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "canonical_title": self.canonical_title,
            "lang": self.lang,
            "extract": self.extract,
            "headings": self.headings(),
        }


@dataclass
class _CacheEntry:
    article: WikipediaArticle
    expires_at: float
    aliases: set[tuple[str, str]]


class WikipediaExtractCache:
    """Thread-safe bounded TTL/LRU cache of Wikipedia extracts.

    Size is the number of canonical articles, not the number of title
    aliases. A redirect adds the requested title onto the canonical
    entry instead of allocating a second slot.

    ``get_or_load`` never holds ``_lock`` while ``loader`` runs (that
    path is Wikipedia HTTP or fixture I/O). Same-key misses single-flight
    via ``_inflight``; a hit on a different warm key is not blocked by
    an in-flight miss. The stub's ``ThreadingHTTPServer`` depends on that.
    """

    def __init__(
        self,
        *,
        maxsize: int | None = None,
        ttl_seconds: float | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.maxsize = max(1, int(maxsize if maxsize is not None else _env_int(CACHE_MAXSIZE_ENV, CACHE_MAXSIZE)))
        self.ttl_seconds = float(
            ttl_seconds if ttl_seconds is not None else _env_float(CACHE_TTL_ENV, CACHE_TTL_SECONDS)
        )
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._order: OrderedDict[tuple[str, str], _CacheEntry] = OrderedDict()
        self._alias: dict[tuple[str, str], tuple[str, str]] = {}
        self._inflight: dict[tuple[str, str], threading.Event] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, title: str, lang: str) -> tuple[str, str]:
        return (lang.strip().casefold() or DEFAULT_LANG, _norm_title(title))

    def _purge_entry_unlocked(self, canonical_key: tuple[str, str]) -> None:
        entry = self._order.pop(canonical_key, None)
        if entry is None:
            return
        for alias in entry.aliases:
            if self._alias.get(alias) == canonical_key:
                del self._alias[alias]

    def _get_unlocked(self, title: str, lang: str) -> WikipediaArticle | None:
        key = self._key(title, lang)
        canonical_key = self._alias.get(key)
        if canonical_key is None:
            return None
        entry = self._order.get(canonical_key)
        if entry is None:
            self._alias.pop(key, None)
            return None
        if entry.expires_at <= self._monotonic():
            self._purge_entry_unlocked(canonical_key)
            return None
        self._order.move_to_end(canonical_key)
        return entry.article

    def _evict_if_needed_unlocked(self) -> None:
        while len(self._order) >= self.maxsize:
            oldest, _entry = self._order.popitem(last=False)
            for alias in _entry.aliases:
                if self._alias.get(alias) == oldest:
                    del self._alias[alias]

    def _put_unlocked(self, article: WikipediaArticle, aliases: Iterable[str]) -> WikipediaArticle:
        lang = article.lang
        canonical_key = self._key(article.canonical_title, lang)
        alias_keys = {canonical_key}
        for title in aliases:
            if title and title.strip():
                alias_keys.add(self._key(title, lang))

        existing_canonical = self._alias.get(canonical_key)
        if existing_canonical is not None:
            existing = self._order.get(existing_canonical)
            if existing is not None and existing.expires_at > self._monotonic():
                for alias in alias_keys:
                    existing.aliases.add(alias)
                    self._alias[alias] = existing_canonical
                self._order.move_to_end(existing_canonical)
                return existing.article
            if existing_canonical in self._order:
                self._purge_entry_unlocked(existing_canonical)

        self._evict_if_needed_unlocked()
        entry = _CacheEntry(
            article=article,
            expires_at=self._monotonic() + self.ttl_seconds,
            aliases=set(alias_keys),
        )
        self._order[canonical_key] = entry
        for alias in alias_keys:
            previous = self._alias.get(alias)
            if previous is not None and previous != canonical_key:
                prev_entry = self._order.get(previous)
                if prev_entry is not None:
                    prev_entry.aliases.discard(alias)
            self._alias[alias] = canonical_key
        return article

    def get(self, title: str, lang: str = DEFAULT_LANG) -> WikipediaArticle | None:
        with self._lock:
            return self._get_unlocked(title, lang)

    def put(self, article: WikipediaArticle, aliases: Iterable[str] = ()) -> WikipediaArticle:
        with self._lock:
            return self._put_unlocked(article, aliases)

    def get_or_load(
        self,
        title: str,
        lang: str,
        loader: Callable[[str, str], tuple[WikipediaArticle, tuple[str, ...]]],
    ) -> tuple[WikipediaArticle, CacheLookup]:
        """Return a cached extract, loading on miss without holding the lock.

        Under the lock we only decide hit / join an in-flight miss / become
        the owner. ``loader`` (HTTP or fixture I/O) always runs outside it.
        Waiters re-check the cache after the owner finishes so a successful
        single-flight is one load; a failed load is not cached and waiters
        may become the next owner.
        """

        key = self._key(title, lang)
        while True:
            wait_for: threading.Event | None = None
            load_event: threading.Event | None = None
            with self._lock:
                cached = self._get_unlocked(title, lang)
                if cached is not None:
                    self.hits += 1
                    return cached, "hit"
                existing = self._inflight.get(key)
                if existing is not None:
                    wait_for = existing
                else:
                    load_event = threading.Event()
                    self._inflight[key] = load_event
                    self.misses += 1
            if wait_for is not None:
                wait_for.wait()
                continue
            assert load_event is not None
            try:
                article, extra_aliases = loader(title, lang)
            except BaseException:
                with self._lock:
                    if self._inflight.get(key) is load_event:
                        del self._inflight[key]
                raise
            else:
                with self._lock:
                    stored = self._put_unlocked(
                        article, (title, article.canonical_title, *extra_aliases)
                    )
                    if self._inflight.get(key) is load_event:
                        del self._inflight[key]
                    return stored, "miss"
            finally:
                load_event.set()

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)

    def clear(self) -> None:
        with self._lock:
            self._order.clear()
            self._alias.clear()
            self.hits = 0
            self.misses = 0
            pending = list(self._inflight.values())
            self._inflight.clear()
        for event in pending:
            event.set()


_CACHE: WikipediaExtractCache | None = None
_CACHE_GUARD = threading.Lock()


def get_article_cache() -> WikipediaExtractCache:
    global _CACHE
    with _CACHE_GUARD:
        if _CACHE is None:
            _CACHE = WikipediaExtractCache()
        return _CACHE


def reset_article_cache(cache: WikipediaExtractCache | None = None) -> WikipediaExtractCache:
    """Replace the process-wide cache. Tests use this; production rarely needs it."""
    global _CACHE
    with _CACHE_GUARD:
        _CACHE = cache if cache is not None else WikipediaExtractCache()
        return _CACHE


def _wiki_headings_to_atx(text: str) -> str:
    """MediaWiki ``== Heading ==`` -> ATX ``## Heading``.

    Wikipedia's plaintext extract never emits a single ``=`` (that would be
    the article's own title, which is not part of the extract body), so
    level 2 is the shallowest marker actually seen; it is kept as ATX
    level 2 to match fixtures/stub_front/wiki_yokohama.md's own shape,
    where the fetched article's title is a separately-added level-1 ATX
    heading wrapping the lead paragraph and every ``==`` section under it.
    """

    def replace(match: re.Match[str]) -> str:
        level = len(match.group(1))
        return f"{'#' * level} {match.group(2)}"

    return _WIKI_HEADING_RE.sub(replace, text)


def _payload_covers_title(payload: dict[str, Any], title: str) -> bool:
    requested = _norm_title(title)
    if not requested:
        return False
    query = payload.get("query") or {}
    for page in (query.get("pages") or {}).values():
        if not isinstance(page, dict):
            continue
        if _norm_title(str(page.get("title") or "")) == requested:
            return True
    for redirect in query.get("redirects") or []:
        if not isinstance(redirect, dict):
            continue
        if _norm_title(str(redirect.get("from") or "")) == requested:
            return True
        if _norm_title(str(redirect.get("to") or "")) == requested:
            return True
    return False


def _redirect_aliases(payload: dict[str, Any]) -> tuple[str, ...]:
    aliases: list[str] = []
    for redirect in (payload.get("query") or {}).get("redirects") or []:
        if not isinstance(redirect, dict):
            continue
        source = str(redirect.get("from") or "").strip()
        if source:
            aliases.append(source)
    return tuple(aliases)


def _parse_extract_payload(payload: dict[str, Any], title: str, lang: str) -> tuple[str, str]:
    pages = (payload.get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), None)
    if not isinstance(page, dict) or "missing" in page:
        raise WikipediaFetchError(f"no {lang}.wikipedia.org article named {title!r}")
    extract = str(page.get("extract") or "")
    canonical_title = str(page.get("title") or title)
    return canonical_title, extract


def _read_fixture_payload(path: Path, title: str, lang: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WikipediaFetchError(f"could not read wikipedia fixture {path}: {exc}") from exc
    if not isinstance(payload, dict) or not _payload_covers_title(payload, title):
        raise WikipediaFetchError(f"no {lang}.wikipedia.org article named {title!r}")
    return payload


def _fetch_extract_payload(title: str, lang: str) -> dict[str, Any]:
    fixture = os.environ.get(FIXTURE_ENV, "").strip()
    if fixture:
        return _read_fixture_payload(Path(fixture), title, lang)

    query = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "wiki",
            "redirects": 1,
            "titles": title,
        }
    )
    url = f"{WIKIPEDIA_API.format(lang=lang)}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WikipediaFetchError(f"could not fetch {title!r} from {lang}.wikipedia.org: {exc}") from exc
    if not isinstance(payload, dict):
        raise WikipediaFetchError(f"could not fetch {title!r} from {lang}.wikipedia.org: bad JSON")
    return payload


def _fetch_extract(title: str, lang: str) -> tuple[str, str]:
    """Return (canonical_title, plaintext_extract_with_wiki_headings)."""
    payload = _fetch_extract_payload(title, lang)
    return _parse_extract_payload(payload, title, lang)


def _load_uncached(title: str, lang: str) -> tuple[WikipediaArticle, tuple[str, ...]]:
    payload = _fetch_extract_payload(title, lang)
    canonical_title, extract = _parse_extract_payload(payload, title, lang)
    article = WikipediaArticle(canonical_title=canonical_title, lang=lang, extract=extract)
    return article, _redirect_aliases(payload)


def load_wikipedia_article(title: str, *, lang: str = DEFAULT_LANG) -> tuple[WikipediaArticle, CacheLookup]:
    """Return the article and whether the process cache already had it."""
    cleaned = title.strip()
    lang = (lang or DEFAULT_LANG).strip() or DEFAULT_LANG
    if not cleaned:
        raise WikipediaFetchError(f"no {lang}.wikipedia.org article named {title!r}")
    return get_article_cache().get_or_load(cleaned, lang, _load_uncached)


def fetch_article_sections(title: str, *, lang: str = DEFAULT_LANG) -> list[Section]:
    """Sections for a real Wikipedia article, via the same ``Section``
    shape (and the same ``parse_sections()``) as every other corpus in
    this repo -- so ``lookup_heading()`` works identically on either."""
    article, _lookup = load_wikipedia_article(title, lang=lang)
    return article.sections()


def fetch_wikipedia_article(title: str, *, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """MCP tool body: ``dispatch_tool("fetch_wikipedia_article", ...)``.

    One structured object: the MediaWiki plaintext extract as returned,
    the canonical title after redirects, and the heading list derived
    from that same extract. Heading switches must not require a second
    network fetch -- that is the process cache, not a second tool.
    """
    article, _lookup = load_wikipedia_article(title, lang=lang)
    return article.as_tool_result()


def fetch_wikipedia_section(title: str, heading: str = "", *, lang: str = DEFAULT_LANG) -> list[dict[str, Any]]:
    """MCP tool body: ``dispatch_tool("fetch_wikipedia_section", ...)``.

    No ``heading`` (or a blank one): return every section's title only --
    the "pulldown" list, deliberately without body text so a caller lists
    options before committing to a possibly-large fetch's body payload.
    A ``heading`` that matches (see ``lookup_heading()`` -- exact first,
    then fuzzy): that one section's title, its ATX form (``heading_markdown``,
    e.g. ``"## Geography"`` -- the section's own level, not hardcoded),
    and its body (everything after that heading up to the next one at the
    same level or shallower, so nested subsections stay inside -- same
    contract as every other ``Section`` in this repo, see
    ``markdown_lib.parse_sections()``). A ``heading`` that
    matches nothing is a normal empty result, not an error -- same
    "valid call, no rows" contract catalog.py's other tools use. A fetch
    that fails outright (network, no such article) raises
    ``WikipediaFetchError``, which the caller lets propagate as an actual
    MCP tool error, same as any other unexpected exception in this catalog.

    Compatibility: the list-of-rows shape is unchanged. The process cache
    means listing headings and then fetching one body is still one HTTP
    call for the same title+language.
    """
    article, _lookup = load_wikipedia_article(title, lang=lang)
    sections = article.sections()
    if not heading.strip():
        return [{"heading": section.title} for section in sections]
    match = lookup_heading(heading, sections, fuzzy=True)
    if match is None:
        return []
    heading_markdown = f"{'#' * match.level} {match.title}"
    return [{"heading": match.title, "heading_markdown": heading_markdown, "body": match.body}]
