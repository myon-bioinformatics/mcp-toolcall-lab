"""Fetch a real Wikipedia article's sections over HTTP.

This is the one tool in this lab's MCP catalog that is **not** a
deterministic offline mock -- every other tool in ``catalog.py`` returns
canned data on purpose (see its own module docstring: "never calls a
live API"). This one calls Wikipedia's own action API for real, so a
heading-pulldown + section-extraction UX can be tried against a genuine
article, not only the vendored ``fixtures/stub_front/*.md`` corpus.

Wikipedia's ``action=query&prop=extracts&explaintext=1&exsectionformat=wiki``
does the heavy lifting: it returns already-plain-text article content
(infoboxes, references, navboxes, and other markup already stripped by
Wikipedia's own extract engine) with MediaWiki ``== Heading ==`` markers.
No HTML or wikitext parsing happens here -- those markers are rewritten
to ATX (``## Heading``) and handed to
``mcp_toolcall_lab.markdown_lib.parse_sections()``, the same
heading-splitter every other corpus in this repo already goes through.
One implementation, not a second one for "real" documents.

This sandbox's own dev environment cannot reach en.wikipedia.org (the
agent proxy denies the CONNECT, same as huggingface.co -- see
scripts/fetch_tiny_cpu_gguf.py's module docstring for that precedent),
so the live path is covered by unit tests against a mocked
``urllib.request.urlopen`` here, plus one real-network integration test
gated behind ``MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1`` for a human or a
CI runner with real internet to opt into -- never on by default, so a
plain ``pytest -q`` never depends on Wikipedia being reachable.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .markdown_lib import Section, lookup_heading, parse_sections

WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
DEFAULT_LANG = "en"
TIMEOUT = 10.0
USER_AGENT = "mcp-toolcall-lab/1.0 (https://github.com/myon-bioinformatics/mcp-toolcall-lab; lab demo, not production traffic)"

_WIKI_HEADING_RE = re.compile(r"^(=+)\s*(.+?)\s*=+\s*$", re.MULTILINE)


class WikipediaFetchError(RuntimeError):
    """The live fetch failed: network, timeout, no such article, or a bad response."""


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


def _fetch_extract(title: str, lang: str) -> tuple[str, str]:
    """Return (canonical_title, plaintext_extract_with_wiki_headings)."""
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

    pages = (payload.get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), None)
    if not isinstance(page, dict) or "missing" in page:
        raise WikipediaFetchError(f"no {lang}.wikipedia.org article named {title!r}")
    extract = str(page.get("extract") or "")
    canonical_title = str(page.get("title") or title)
    return canonical_title, extract


def fetch_article_sections(title: str, *, lang: str = DEFAULT_LANG) -> list[Section]:
    """Sections for a real Wikipedia article, via the same ``Section``
    shape (and the same ``parse_sections()``) as every other corpus in
    this repo -- so ``lookup_heading()`` works identically on either."""
    canonical_title, extract = _fetch_extract(title, lang)
    atx = f"# {canonical_title}\n\n{_wiki_headings_to_atx(extract)}"
    return parse_sections(atx)


def fetch_wikipedia_section(title: str, heading: str = "", *, lang: str = DEFAULT_LANG) -> list[dict[str, Any]]:
    """MCP tool body: ``dispatch_tool("fetch_wikipedia_section", ...)``.

    No ``heading`` (or a blank one): return every section's title only --
    the "pulldown" list, deliberately without body text so a caller lists
    options before committing to a possibly-large fetch's body payload.
    A ``heading`` that matches (see ``lookup_heading()`` -- exact first,
    then fuzzy): that one section's title + body. A ``heading`` that
    matches nothing is a normal empty result, not an error -- same
    "valid call, no rows" contract catalog.py's other tools use. A fetch
    that fails outright (network, no such article) raises
    ``WikipediaFetchError``, which the caller lets propagate as an actual
    MCP tool error, same as any other unexpected exception in this catalog.
    """
    sections = fetch_article_sections(title, lang=lang)
    if not heading.strip():
        return [{"heading": section.title} for section in sections]
    match = lookup_heading(heading, sections, fuzzy=True)
    if match is None:
        return []
    return [{"heading": match.title, "body": match.body}]
