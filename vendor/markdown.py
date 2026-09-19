# markdown.py
"""Stdlib-only Markdown utility functions.

This module is intentionally a single file with no CLI / ``main`` entry point.
It focuses on reusable helpers for Markdown I/O, structure extraction, and
conservative HTML <-> Markdown conversions (links, images, common tags).

It is **not** a full CommonMark implementation. Prefer the inventory helpers
and tests to see what is and is not handled.
"""

from __future__ import annotations

__all__ = [
    "save_markdown",
    "read_markdown",
    "extract_sections",
    "split_sections",
    "extract_links",
    "extract_images",
    "extract_code_blocks",
    "extract_raw_html",
    "extract_urls",
    "inventory",
    "make_link",
    "make_image",
    "html_image_to_markdown",
    "markdown_image_to_html",
    "html_link_to_markdown",
    "markdown_link_to_html",
    "html_to_markdown",
    "markdown_to_html",
    "is_probably_url",
    "heading",
    "bold",
    "italic",
    "strikethrough",
    "blockquote",
    "horizontal_rule",
    "bullet_list",
    "numbered_list",
    "code_block",
    "table",
    "key_value_table",
    "section",
    "inline_code",
    "json_block",
    "status_line",
    "wrap_section",
    "md_table",
    "md_kv",
    "SUPPORTED",
    "UNSUPPORTED",
]

import html as html_module
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Capability notes (kept in-module so a vendored single file stays honest)
# ---------------------------------------------------------------------------

SUPPORTED = {
    "io": ["save_markdown", "read_markdown"],
    "structure": [
        "ATX headings (# ## ...)",
        "fenced code blocks",
        "inline links [text](url)",
        "reference-style link definitions [id]: url",
        "images ![alt](url)",
        "raw HTML tags (best-effort extraction)",
        "horizontal rules (--- *** ___)",
    ],
    "conversion": [
        "link/image builders",
        "HTML <a>/<img> <-> Markdown link/image",
        "conservative html_to_markdown / markdown_to_html for common tags",
    ],
    "generation": [
        "heading / bold / italic / strikethrough / blockquote / horizontal_rule",
        "bullet_list / numbered_list",
        "inline_code / code_block / json_block",
        "table / key_value_table",
        "md_table / md_kv (*args-friendly wrappers, no list/dict pre-building needed)",
        "status_line",
        "section (heading + blocks) / wrap_section (tool-detectable markers)",
    ],
}

UNSUPPORTED = {
    "parser": [
        "full CommonMark / GFM compliance",
        "backslash escaping (\\* stays literal, does not suppress emphasis)",
        "blockquotes (> lines are not parsed into <blockquote>; they escape "
        "and flatten into a plain paragraph, same fallback as tables/task lists)",
        "GitHub-style alerts (> [!NOTE] etc. -- built on the same unsupported blockquote syntax)",
        "nested emphasis edge cases (asymmetric delimiter runs, whitespace-adjacent "
        "delimiters -- see fixtures/benchmark/commonmark_examples.yaml)",
        "tables (GFM)",
        "task lists",
        "footnotes",
        "autolinks (bare URLs and <url>; only [text](url) is recognized)",
        "strikethrough parsing in markdown_to_html (~~x~~ stays literal; "
        "the strikethrough() generator still produces valid ~~x~~ output)",
        "Math / mermaid rendering",
    ],
    "conversion": [
        "lossy round-trips for complex nested HTML",
        "JavaScript / SVG behavior preservation",
        "CSS class and style fidelity",
        "raw inline HTML tags are escaped, not passed through, by markdown_to_html",
    ],
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(?:\s*[-*_]){3,}\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_INLINE_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)"
)
_INLINE_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)"
)
_LINKED_IMAGE_RE = re.compile(
    r"\[(?:!\[[^\]]*\]\([^)]+\))\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)"
)
_REF_DEF_RE = re.compile(
    r"^\s*\[([^\]]+)\]:\s*(\S+)(?:\s+\"([^\"]*)\")?\s*$"
)
_REF_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\[([^\]]*)\]")
_REF_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\[([^\]]+)\]")
_ANGLE_URL_RE = re.compile(r"<(https?://[^>\s]+)>")
_BARE_URL_RE = re.compile(r"(?<![\"'(\\[])(https?://[^\s)<>\"]+)")
_HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>", re.DOTALL)
_HTML_IMG_RE = re.compile(
    r"<img\b([^>]*)/?>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_A_RE = re.compile(
    r"<a\b([^>]*)>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r"""([^\s=]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))""",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def save_markdown(content: str, filepath: str) -> str:
    """Save Markdown content to a file."""
    try:
        path = Path(filepath).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error saving file: {e}"
    return f"Saved successfully: {path}"


def read_markdown(filepath: str, count_hashtags: bool = False) -> dict:
    """Read a Markdown file and optionally count heading ``#`` markers.

    Returns:
        dict with keys ``content`` (str), ``hashtag_count`` (int | None),
        ``success`` (bool).
    """
    result: dict[str, Any] = {
        "content": "",
        "hashtag_count": None,
        "success": False,
    }
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        result["content"] = content
        result["success"] = True
        if count_hashtags:
            total = 0
            for line in content.splitlines():
                match = re.match(r"^(#+)", line)
                if match:
                    total += len(match.group(1))
            result["hashtag_count"] = total
    except OSError as e:
        result["content"] = f"Error reading file: {e}"
    return result


# ---------------------------------------------------------------------------
# Structure extraction
# ---------------------------------------------------------------------------

def extract_sections(content: str) -> list[dict[str, Any]]:
    """Extract ATX heading names from Markdown content."""
    sections: list[dict[str, Any]] = []
    for line in content.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            sections.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                }
            )
    return sections


def split_sections(content: str) -> list[dict[str, Any]]:
    """Split content into heading-delimited sections.

    The prelude before the first heading is returned with ``level`` 0 and
    an empty ``title`` when it is non-empty.
    """
    lines = content.splitlines(keepends=True)
    parts: list[dict[str, Any]] = []
    current = {"level": 0, "title": "", "content": ""}
    body: list[str] = []

    def flush() -> None:
        text = "".join(body)
        if current["level"] == 0 and current["title"] == "" and text.strip() == "":
            return
        item = dict(current)
        item["content"] = text
        parts.append(item)

    for line in lines:
        match = _HEADING_RE.match(line.rstrip("\n"))
        if match:
            flush()
            current = {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "content": "",
            }
            body = [line]
        else:
            body.append(line)
    flush()
    return parts


def _parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(attr_text):
        key = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        attrs[key] = html_module.unescape(value)
    return attrs


def _reference_map(content: str) -> dict[str, dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    for line in content.splitlines():
        match = _REF_DEF_RE.match(line)
        if match:
            key = match.group(1).strip().lower()
            refs[key] = {
                "url": match.group(2).strip("<>"),
                "title": match.group(3) or "",
            }
    return refs


def extract_links(content: str) -> list[dict[str, Any]]:
    """Extract inline and reference-style links (not bare images)."""
    refs = _reference_map(content)
    links: list[dict[str, Any]] = []

    # Linked images: [![alt](img)](href) — record the outer link.
    for match in _LINKED_IMAGE_RE.finditer(content):
        links.append(
            {
                "text": "",
                "url": match.group(1),
                "title": match.group(2) or "",
                "style": "linked-image",
            }
        )

    # Mask constructs that would confuse the inline-link regex.
    masked = _LINKED_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), content)
    masked = _INLINE_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), masked)
    masked = _REF_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), masked)

    for match in _INLINE_LINK_RE.finditer(masked):
        text = match.group(1)
        links.append(
            {
                "text": text,
                "url": match.group(2),
                "title": match.group(3) or "",
                "style": "inline",
            }
        )

    for match in _REF_LINK_RE.finditer(masked):
        text = match.group(1)
        key = (match.group(2) or text).strip().lower()
        ref = refs.get(key, {})
        links.append(
            {
                "text": text,
                "url": ref.get("url", ""),
                "title": ref.get("title", ""),
                "style": "reference",
                "ref": key,
            }
        )

    for match in _HTML_A_RE.finditer(content):
        attrs = _parse_attrs(match.group(1))
        href = attrs.get("href", "")
        if href:
            links.append(
                {
                    "text": re.sub(r"<[^>]+>", "", match.group(2)).strip(),
                    "url": href,
                    "title": attrs.get("title", ""),
                    "style": "html",
                }
            )
    return links


def extract_images(content: str) -> list[dict[str, Any]]:
    """Extract Markdown and HTML images."""
    refs = _reference_map(content)
    images: list[dict[str, Any]] = []

    for match in _INLINE_IMAGE_RE.finditer(content):
        images.append(
            {
                "alt": match.group(1),
                "url": match.group(2),
                "title": match.group(3) or "",
                "style": "inline",
            }
        )

    for match in _REF_IMAGE_RE.finditer(content):
        alt = match.group(1)
        key = match.group(2).strip().lower()
        ref = refs.get(key, {})
        images.append(
            {
                "alt": alt,
                "url": ref.get("url", ""),
                "title": ref.get("title", ""),
                "style": "reference",
                "ref": key,
            }
        )

    for match in _HTML_IMG_RE.finditer(content):
        attrs = _parse_attrs(match.group(1))
        src = attrs.get("src", "")
        if src:
            images.append(
                {
                    "alt": attrs.get("alt", ""),
                    "url": src,
                    "title": attrs.get("title", ""),
                    "style": "html",
                    "width": attrs.get("width"),
                    "height": attrs.get("height"),
                }
            )
    return images


def extract_code_blocks(content: str) -> list[dict[str, Any]]:
    """Extract fenced code blocks (``` or ~~~)."""
    blocks: list[dict[str, Any]] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        match = _FENCE_RE.match(lines[i])
        if not match:
            i += 1
            continue
        fence = match.group(1)
        info = match.group(2).strip()
        lang = info.split()[0] if info else ""
        body: list[str] = []
        i += 1
        while i < len(lines):
            if lines[i].startswith(fence):
                break
            body.append(lines[i])
            i += 1
        blocks.append(
            {
                "language": lang,
                "info": info,
                "code": "\n".join(body),
            }
        )
        i += 1
    return blocks


def extract_raw_html(content: str) -> list[dict[str, Any]]:
    """Best-effort list of raw HTML tags found in the document."""
    found: list[dict[str, Any]] = []
    for match in _HTML_TAG_RE.finditer(content):
        tag = match.group(1).lower()
        snippet = match.group(0)
        # Skip tags that appear inside fenced code by a cheap heuristic:
        # handled via inventory after code stripping when needed.
        found.append({"tag": tag, "snippet": snippet})
    return found


def extract_urls(content: str, *, base_url: str | None = None) -> list[str]:
    """Collect unique URLs from links, images, autolinks, and bare URLs."""
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        url = url.strip()
        if not url:
            return
        if base_url:
            url = urljoin(base_url, url)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for item in extract_links(content):
        add(item.get("url", ""))
    for item in extract_images(content):
        add(item.get("url", ""))
    for match in _ANGLE_URL_RE.finditer(content):
        add(match.group(1))
    for match in _BARE_URL_RE.finditer(content):
        add(match.group(1).rstrip(".,;:)"))
    return urls


def inventory(content: str) -> dict[str, Any]:
    """Summarize common Markdown constructs present in ``content``."""
    sections = extract_sections(content)
    links = extract_links(content)
    images = extract_images(content)
    codes = extract_code_blocks(content)
    # Avoid counting HTML that only appears inside fenced code.
    scrubbed = content
    for block in codes:
        scrubbed = scrubbed.replace(block["code"], "")
    html_tags = extract_raw_html(scrubbed)
    hr_count = sum(1 for line in content.splitlines() if _HR_RE.match(line))
    return {
        "headings": sections,
        "heading_count": len(sections),
        "links": links,
        "link_count": len(links),
        "images": images,
        "image_count": len(images),
        "code_blocks": codes,
        "code_block_count": len(codes),
        "raw_html": html_tags,
        "raw_html_count": len(html_tags),
        "horizontal_rule_count": hr_count,
        "urls": extract_urls(content),
        "supported": SUPPORTED,
        "unsupported": UNSUPPORTED,
    }


# ---------------------------------------------------------------------------
# Builders & focused HTML <-> Markdown helpers
# ---------------------------------------------------------------------------

def make_link(text: str, url: str, title: str | None = None) -> str:
    """Build a Markdown inline link."""
    if title:
        return f'[{text}]({url} "{title}")'
    return f"[{text}]({url})"


def make_image(alt: str, url: str, title: str | None = None) -> str:
    """Build a Markdown image."""
    if title:
        return f'![{alt}]({url} "{title}")'
    return f"![{alt}]({url})"


def html_image_to_markdown(html: str) -> str:
    """Convert an ``<img>`` tag (or HTML snippet containing one) to Markdown."""
    match = _HTML_IMG_RE.search(html)
    if not match:
        return ""
    attrs = _parse_attrs(match.group(1))
    return make_image(attrs.get("alt", ""), attrs.get("src", ""), attrs.get("title") or None)


def markdown_image_to_html(
    markdown: str,
    *,
    width: str | None = None,
    height: str | None = None,
) -> str:
    """Convert a Markdown image (or image URL string) to an ``<img>`` tag."""
    match = _INLINE_IMAGE_RE.search(markdown.strip())
    if match:
        alt, url, title = match.group(1), match.group(2), match.group(3) or ""
    else:
        # Treat bare path/URL as image source.
        alt, url, title = "", markdown.strip(), ""
    attrs = [
        f'src="{html_module.escape(url, quote=True)}"',
        f'alt="{html_module.escape(alt, quote=True)}"',
    ]
    if title:
        attrs.append(f'title="{html_module.escape(title, quote=True)}"')
    if width:
        attrs.append(f'width="{html_module.escape(width, quote=True)}"')
    if height:
        attrs.append(f'height="{html_module.escape(height, quote=True)}"')
    return "<img " + " ".join(attrs) + " />"


def html_link_to_markdown(html: str) -> str:
    """Convert an ``<a>...</a>`` tag to a Markdown link."""
    match = _HTML_A_RE.search(html)
    if not match:
        return ""
    attrs = _parse_attrs(match.group(1))
    text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
    return make_link(text, attrs.get("href", ""), attrs.get("title") or None)


def markdown_link_to_html(markdown: str) -> str:
    """Convert a Markdown inline link to an ``<a>`` tag."""
    match = _INLINE_LINK_RE.search(markdown.strip())
    if not match:
        return ""
    text, url, title = match.group(1), match.group(2), match.group(3) or ""
    attrs = [f'href="{html_module.escape(url, quote=True)}"']
    if title:
        attrs.append(f'title="{html_module.escape(title, quote=True)}"')
    return f"<a {' '.join(attrs)}>{html_module.escape(text)}</a>"


# ---------------------------------------------------------------------------
# Markdown generation building blocks
# ---------------------------------------------------------------------------

def heading(text: str, level: int = 1) -> str:
    """Build an ATX heading (``level`` is clamped to 1-6).

    >>> heading("Title")
    '# Title\\n'
    >>> heading("Sub", level=2)
    '## Sub\\n'
    """
    level = max(1, min(level, 6))
    return "#" * level + " " + str(text) + "\n"


def bold(text: str) -> str:
    """Wrap ``text`` in ``**bold**`` markers."""
    return f"**{text}**"


def italic(text: str) -> str:
    """Wrap ``text`` in ``*italic*`` markers."""
    return f"*{text}*"


def strikethrough(text: str) -> str:
    """Wrap ``text`` in ``~~strikethrough~~`` markers."""
    return f"~~{text}~~"


def blockquote(text: str) -> str:
    """Prefix every line of ``text`` with ``> `` to form a blockquote."""
    lines = str(text).splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines) + "\n"


def horizontal_rule() -> str:
    """Return a thematic break (``---``)."""
    return "---\n"


def bullet_list(items: Any) -> str:
    """Build an unordered (``-``) list from an iterable of strings."""
    lines = [f"- {item}" for item in items]
    return "\n".join(lines) + ("\n" if lines else "")


def numbered_list(items: Any) -> str:
    """Build an ordered (``1.``) list from an iterable of strings."""
    lines = [f"{i}. {item}" for i, item in enumerate(items, start=1)]
    return "\n".join(lines) + ("\n" if lines else "")


def inline_code(text: str) -> str:
    """Wrap ``text`` in single backticks for inline code."""
    return f"`{text}`"


_FENCE_CHARS = ("`", "~")


def _adaptive_fence(code: str, fence_char: str = "`") -> str:
    """A fence of ``fence_char`` one longer than the longest run already in ``code``.

    ``_FENCE_RE`` (and this module's own fence-closing scan) accept any
    length-3-or-more fence, but only close on a line starting with a run of
    the same fence character at least as long as the opening fence -- so a
    fixed triple-backtick fence is ambiguous the moment ``code`` itself
    contains a triple-backtick run (e.g. Markdown source shown as an example
    inside a code block). Picking a fence longer than anything already in
    ``code`` keeps that scan unambiguous with no changes to the reading side.

    ``fence_char`` must be a single backtick or tilde -- the only two
    characters Markdown recognizes as a fence at all (``_FENCE_RE``). Any
    other value raises ``ValueError`` rather than silently building a fence
    nothing would ever close (a multi-character ``fence_char`` isn't a valid
    fence line, and an empty one breaks the run-length regex outright).
    """
    if fence_char not in _FENCE_CHARS:
        raise ValueError(f"fence_char must be one of {_FENCE_CHARS!r}, got {fence_char!r}")
    longest = 0
    for run in re.findall(re.escape(fence_char) + r"+", code):
        longest = max(longest, len(run))
    return fence_char * max(3, longest + 1)


def code_block(code: str, lang: str = "", *, fence_char: str = "`") -> str:
    """Wrap ``code`` in a fenced code block, optionally tagged with ``lang``.

    The fence length adapts to ``code``'s content: if it already contains a
    run of ``fence_char`` as long as (or longer than) a plain triple fence
    -- typically Markdown-inside-Markdown, like a fenced example embedded in
    a doc -- the fence grows just enough to stay unambiguous, the same way
    CommonMark itself requires. Plain content keeps the original triple
    fence, so existing callers see no change. Pass ``fence_char="~"`` for a
    tilde fence (e.g. when ``code`` itself contains backtick runs).

    :raises ValueError: if ``fence_char`` isn't ``"`"`` or ``"~"`` -- the
        only two characters Markdown recognizes as a fence.
    """
    fence = _adaptive_fence(code, fence_char)
    return f"{fence}{lang}\n{code}\n{fence}\n"


def json_block(obj: Any, indent: int = 2) -> str:
    """Serialize ``obj`` as JSON and wrap it in a ```json fenced code block."""
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    return code_block(text, lang="json")


def table(headers: Any, rows: Any) -> str:
    """Build a Markdown (GFM-style) table.

    ``headers`` is a sequence of column names; ``rows`` is a sequence of
    sequences of cell values (converted with ``str``). Short rows are padded
    with empty cells; extra cells beyond ``len(headers)`` are dropped.

    >>> table(["a", "b"], [[1, 2], [3, 4]])
    '| a | b |\\n| --- | --- |\\n| 1 | 2 |\\n| 3 | 4 |\\n'
    """
    headers = [str(h) for h in headers]
    if not headers:
        return ""
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        cells = [str(v) for v in row][: len(headers)]
        cells += [""] * (len(headers) - len(cells))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def key_value_table(
    data: Any,
    key_label: str = "Key",
    value_label: str = "Value",
) -> str:
    """Render a mapping as a two-column Markdown table via ``table``."""
    rows = [[key, value] for key, value in dict(data).items()]
    return table([key_label, value_label], rows)


def _record_to_cells(record: Any) -> list[str]:
    if isinstance(record, dict):
        return [str(v) for v in record.values()]
    if isinstance(record, (list, tuple)):
        return [str(v) for v in record]
    return [str(record)]


def md_table(*headers_and_rows: Any) -> str:
    """``*args`` version of ``table`` — pass values directly, no list needed.

    Dispatches on the first argument:

    - a ``dict`` -> every argument is a record; headers come from the first
      record's keys, and each record's ``.values()`` become that row's cells.
    - a ``list``/``tuple`` -> treated as the header row; the remaining
      arguments are data rows (each a list/tuple of cells, a dict of values,
      or a single scalar rendered as a one-cell row).
    - anything else -> every argument becomes a single-cell row under a
      generic ``value`` header.

    >>> md_table(["name", "val"], ["loss", "0.01"], ["iou", "0.85"])
    '| name | val |\\n| --- | --- |\\n| loss | 0.01 |\\n| iou | 0.85 |\\n'
    >>> md_table({"name": "loss", "val": "0.01"}, {"name": "iou", "val": "0.85"})
    '| name | val |\\n| --- | --- |\\n| loss | 0.01 |\\n| iou | 0.85 |\\n'
    """
    if not headers_and_rows:
        return ""

    first = headers_and_rows[0]
    if isinstance(first, dict):
        headers = [str(k) for k in first.keys()]
        rows = [_record_to_cells(rec) for rec in headers_and_rows]
        return table(headers, rows)

    if isinstance(first, (list, tuple)):
        headers = [str(h) for h in first]
        rows = [_record_to_cells(rec) for rec in headers_and_rows[1:]]
        return table(headers, rows)

    return table(["value"], [[str(v)] for v in headers_and_rows])


def md_kv(*pairs: Any, key_label: str = "Key", value_label: str = "Value") -> str:
    """``*args`` version of ``key_value_table`` — pass ``key, value`` pairs or dicts.

    Alternating positional ``key, value`` arguments and ``dict`` arguments can
    be freely mixed; a trailing key with no value gets an empty string.

    >>> md_kv("mode", "train", "epochs", 10)
    '| Key | Value |\\n| --- | --- |\\n| mode | train |\\n| epochs | 10 |\\n'
    >>> md_kv({"mode": "train"}, "device", "cuda")
    '| Key | Value |\\n| --- | --- |\\n| mode | train |\\n| device | cuda |\\n'
    """
    merged: dict[str, Any] = {}
    it = iter(pairs)
    for item in it:
        if isinstance(item, dict):
            merged.update(item)
        else:
            merged[str(item)] = next(it, "")
    return key_value_table(merged, key_label=key_label, value_label=value_label)


def status_line(ok: bool, msg_ok: str, msg_ng: str) -> str:
    """Build a one-line status message, prefixed with a checkmark or warning."""
    prefix = "✓" if ok else "⚠"
    return f"{prefix} {msg_ok if ok else msg_ng}\n"


def section(title: str, blocks: Any, level: int = 2) -> str:
    """Join a heading with a sequence of pre-rendered Markdown blocks."""
    return heading(title, level=level) + "".join(blocks)


def wrap_section(name: str, content: str) -> str:
    """Wrap ``content`` in HTML-comment markers so tools/LLMs can find section boundaries."""
    content = content if content.endswith("\n") else content + "\n"
    return f"<!-- BEGIN_SECTION:{name} -->\n{content}<!-- END_SECTION:{name} -->\n"


# ---------------------------------------------------------------------------
# Conservative HTML <-> Markdown (common tags only)
# ---------------------------------------------------------------------------

class _HTMLToMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._list_stack: list[str] = []
        self._li_index: list[int] = []
        self._in_pre = False
        self._in_code = False
        self._suppress = 0
        self._link_href = ""
        self._link_title = ""
        self._link_open = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in {"script", "style"}:
            self._suppress += 1
            return
        if self._suppress:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self.parts.append("\n\n" + ("#" * level) + " ")
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("  \n")
        elif tag == "hr":
            self.parts.append("\n\n---\n\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
            self._in_code = True
        elif tag == "pre":
            self._in_pre = True
            self.parts.append("\n\n```\n")
        elif tag == "a":
            self.parts.append("[")
            self._link_href = attr.get("href", "")
            self._link_title = attr.get("title", "")
            self._link_open = True
        elif tag == "img":
            self.parts.append(
                make_image(
                    attr.get("alt", ""),
                    attr.get("src", ""),
                    attr.get("title") or None,
                )
            )
        elif tag in {"ul", "ol"}:
            self._list_stack.append(tag)
            self._li_index.append(0)
            self.parts.append("\n")
        elif tag == "li":
            depth = max(len(self._list_stack) - 1, 0)
            indent = "  " * depth
            kind = self._list_stack[-1] if self._list_stack else "ul"
            if kind == "ol":
                self._li_index[-1] += 1
                bullet = f"{self._li_index[-1]}."
            else:
                bullet = "-"
            self.parts.append(f"\n{indent}{bullet} ")
        elif tag == "blockquote":
            self.parts.append("\n\n> ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self.parts.append("\n\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
            self._in_code = False
        elif tag == "pre":
            self.parts.append("\n```\n\n")
            self._in_pre = False
        elif tag == "a" and self._link_open:
            if self._link_title:
                self.parts.append(f']({self._link_href} "{self._link_title}")')
            else:
                self.parts.append(f"]({self._link_href})")
            self._link_open = False
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            if self._li_index:
                self._li_index.pop()
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        if self._in_pre or self._in_code:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def output(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def html_to_markdown(html: str) -> str:
    """Conservatively convert common HTML tags to Markdown."""
    parser = _HTMLToMarkdownParser()
    parser.feed(html)
    parser.close()
    return parser.output()


def _escape_html_text(text: str) -> str:
    return html_module.escape(text, quote=False)


def markdown_to_html(content: str) -> str:
    """Conservatively convert a Markdown *subset* to HTML.

    Handles ATX headings, fenced code, paragraphs, inline code, bold/italic,
    links, images, thematic breaks, and simple bullet/numbered lists.
    """
    lines = content.splitlines()
    out: list[str] = []
    i = 0
    in_list: str | None = None

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def render_inline(text: str) -> str:
        placeholders: list[str] = []

        def stash(snippet: str) -> str:
            placeholders.append(snippet)
            return f"\x00PH{len(placeholders) - 1}\x00"

        text = _INLINE_IMAGE_RE.sub(
            lambda m: stash(markdown_image_to_html(m.group(0))),
            text,
        )
        text = _INLINE_LINK_RE.sub(
            lambda m: stash(markdown_link_to_html(m.group(0))),
            text,
        )
        text = re.sub(
            r"`([^`]+)`",
            lambda m: stash(f"<code>{_escape_html_text(m.group(1))}</code>"),
            text,
        )
        text = _escape_html_text(text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
        for index, snippet in enumerate(placeholders):
            text = text.replace(f"\x00PH{index}\x00", snippet)
        return text

    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            close_list()
            fence_mark = fence.group(1)
            info = fence.group(2).strip()
            lang = info.split()[0] if info else ""
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith(fence_mark):
                body.append(lines[i])
                i += 1
            code = _escape_html_text("\n".join(body))
            cls = f' class="language-{html_module.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{code}\n</code></pre>")
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            close_list()
            level = len(heading.group(1))
            title = render_inline(heading.group(2).strip())
            out.append(f"<h{level}>{title}</h{level}>")
            i += 1
            continue

        if _HR_RE.match(line):
            close_list()
            out.append("<hr />")
            i += 1
            continue

        ul = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        ol = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if ul or ol:
            kind = "ul" if ul else "ol"
            item = (ul or ol).group(2)  # type: ignore[union-attr]
            if in_list != kind:
                close_list()
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{render_inline(item)}</li>")
            i += 1
            continue

        if not line.strip():
            close_list()
            i += 1
            continue

        close_list()
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not _HEADING_RE.match(lines[i]) and not _FENCE_RE.match(lines[i]) and not _HR_RE.match(lines[i]) and not re.match(r"^(\s*)[-*+]\s+", lines[i]) and not re.match(r"^(\s*)\d+\.\s+", lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{render_inline(' '.join(s.strip() for s in para))}</p>")

    close_list()
    return "\n".join(out) + ("\n" if out else "")


def is_probably_url(value: str) -> bool:
    """Return True if ``value`` looks like an http(s) URL."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
