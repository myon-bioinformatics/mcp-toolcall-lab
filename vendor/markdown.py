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
    "extract_section",
    "strip_prose_keep_structure",
    "minify_markdown",
    "safe_truncate",
    "inventory",
    "make_link",
    "make_image",
    "html_image_to_markdown",
    "markdown_image_to_html",
    "html_link_to_markdown",
    "markdown_link_to_html",
    "html_to_markdown",
    "markdown_to_html",
    "markdown_to_kramdown",
    "kramdown_to_markdown",
    "ial",
    "with_attributes",
    "alert_stylesheet",
    "default_stylesheet",
    "is_probably_url",
    "heading",
    "bold",
    "italic",
    "strikethrough",
    "blockquote",
    "alert",
    "horizontal_rule",
    "bullet_list",
    "numbered_list",
    "task_item",
    "task_list",
    "details",
    "footnote_ref",
    "footnote",
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
    "ALERT_FLAVORS",
    "GITHUB_ALERT_KINDS",
    "QIITA_NOTE_KINDS",
    "ZENN_MESSAGE_KINDS",
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
        "context helpers: extract_section / strip_prose_keep_structure / minify_markdown / safe_truncate",
    ],
    "conversion": [
        "link/image builders",
        "HTML <a>/<img> <-> Markdown link/image",
        "conservative html_to_markdown / markdown_to_html for common tags",
        "GitHub / Qiita / Zenn / Obsidian alerts and callouts in markdown_to_html "
        "(shared <aside class=\"markdown-alert\"> HTML; alerts win over ordinary > quotes)",
        "ordinary > blockquotes in markdown_to_html (multi-line, blank > lines; "
        "inner ATX/lists/tables stay paragraph text)",
        "simple GFM pipe tables in markdown_to_html "
        "(header + | --- | separator + rows -> <table>/<thead>/<th>/<tbody>/<td>)",
        "strikethrough in markdown_to_html (~~text~~ -> <del>text</del>)",
        "GFM task lists in markdown_to_html "
        "(- [ ] / - [x] / - [X], also * and +; disabled checkbox <input>; mixed with ordinary <li> in one <ul>)",
        "angle-bracket autolinks in markdown_to_html "
        "(<https://...> / <http://...> -> <a href>; bare URLs stay literal)",
        ":::details summary containers in markdown_to_html "
        "(Zenn-style :::details ... ::: -> <details><summary>; raw HTML <details> stays escaped)",
        "GFM/Pandoc-like footnotes in markdown_to_html "
        "([^id] refs + [^id]: definitions -> <sup><a> + <section class=\"footnotes\">)",
        "simple HTML <table> in html_to_markdown (GFM pipe table; headerless rows use an empty header)",
        "HTML <del> -> ~~text~~ and <li><input type=checkbox> -> - [ ] / - [x] in html_to_markdown",
        "heading/paragraph id+class (+ other simple attrs) in html_to_markdown "
        "as Pandoc-style {#id .class key=\"value\"} so markdown_to_kramdown can attach IAL",
        "markdown_to_kramdown: Pandoc/PHP-Extra {#id .class key=value} on headings/paragraphs "
        "-> Kramdown block IAL ({: #id .class key=\"value\"}); ordinary Markdown left alone",
        "kramdown_to_markdown: strip known heading/paragraph IAL back to plain Markdown "
        "(attributes dropped; {:toc} / {::extensions} / Liquid / front matter left as-is)",
        "alert_stylesheet / default_stylesheet (compact CSS strings for markdown_to_html output)",
    ],
    "generation": [
        "heading / bold / italic / strikethrough / blockquote / horizontal_rule",
        "alert (GitHub > [!NOTE], Qiita :::note, Zenn :::message, Obsidian callouts; "
        "GitLab > [!note] generation)",
        "bullet_list / numbered_list / task_item / task_list",
        "details (Zenn :::details summary / body; markdown_to_html -> <details><summary>)",
        "footnote_ref / footnote ([^id] inline ref and [^id]: definition)",
        "inline_code / code_block / json_block",
        "table / key_value_table",
        "md_table / md_kv (*args-friendly wrappers, no list/dict pre-building needed)",
        "status_line",
        "section (heading + blocks) / wrap_section (tool-detectable markers)",
        "ial / with_attributes (Kramdown {: #id .class key=\"value\"} lines)",
    ],
}

UNSUPPORTED = {
    "parser": [
        "full CommonMark / GFM compliance",
        "backslash escaping (\\* stays literal, does not suppress emphasis)",
        "lazy blockquote continuation (a quoted paragraph must keep > on every line)",
        "nested blockquotes / inner block constructs inside > quotes "
        "(headings, lists, and tables inside a quote stay paragraph text; "
        "GitHub/Obsidian [!type] openers still render as <aside> and win over quotes)",
        "GitLab >>> multiline alert blockquotes (the > [!note] form is generated; "
        "title-less lowercase five-kind markers parse as Obsidian, not a separate GitLab flavor)",
        "nested alert/callout containers (Obsidian > > [!type], Zenn ::::details, Qiita nested :::)",
        "nested emphasis edge cases (asymmetric delimiter runs, whitespace-adjacent "
        "delimiters -- see fixtures/benchmark/commonmark_examples.yaml)",
        "GFM tables without a delimiter row of 3+ hyphens (header-only pipe lines "
        "stay paragraphs; alignment colons are accepted but not emitted as HTML attributes)",
        "html_to_markdown nested tables / colspan / rowspan / <caption> "
        "(nested tables flatten to cell text; extra spans are ignored; caption text is dropped)",
        "numbered-list task items (1. [ ] stays ordinary <li> text; only -/*/ + markers are tasks)",
        "raw HTML <details> in Markdown (escaped, not passed through; use details() / :::details)",
        "nested :::details / ::::details containers (the first ::: closes the block)",
        "footnote lazy continuation (unindented wrapping stays a new paragraph; "
        "only 2+ space / tab indented continuation is kept). Unused definitions are "
        "dropped. Undefined [^id] stays literal. Duplicate ids: first definition wins",
        "bare URL autolinks (https://example.com stays literal; only <https://...> / "
        "<http://...> and [text](url) become <a>)",
        "angle autolinks with schemes other than http/https (mailto:, ftp:, uppercase HTTP://)",
        "unmatched strikethrough (a lone ~~ stays literal; ~~a~~b~~ takes the first pair)",
        "Math / mermaid rendering",
        "full Kramdown (extensions {::comment}/{::options}/{::nomarkdown}, math, "
        "TOC macros {:toc}, span IAL, IAL on lists/quotes/tables, attribute references)",
        "Liquid {% %} / {{ }}, YAML front matter, Jekyll tags / includes / baseurl",
    ],
    "conversion": [
        "lossy round-trips for complex nested HTML",
        "JavaScript / SVG behavior preservation",
        "CSS class and style fidelity (HTML style= is dropped; IAL class names are kept)",
        "raw inline HTML tags are escaped, not passed through, by markdown_to_html",
        "kramdown_to_markdown drops IAL attributes (lossy); markdown_to_html does not "
        "apply IAL as HTML id/class",
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

# GitHub docs: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#alerts
# Qiita cheat sheet (Note): https://qiita.com/Qiita/items/c686397e4a0f4f11683d
# Zenn Markdown guide (メッセージ): https://zenn.dev/zenn/articles/markdown-guide
# Obsidian Help (Callouts): https://help.obsidian.md/callouts
# GitLab GLFM alerts: https://docs.gitlab.com/ee/user/markdown/#alerts
ALERT_FLAVORS = ("github", "qiita", "zenn", "obsidian", "gitlab")
GITHUB_ALERT_KINDS = ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION")
QIITA_NOTE_KINDS = ("info", "warn", "alert")
ZENN_MESSAGE_KINDS = ("message", "alert")
_GITHUB_ALERT_KIND_SET = frozenset(GITHUB_ALERT_KINDS)
_QIITA_NOTE_KIND_SET = frozenset(QIITA_NOTE_KINDS)
_OBSIDIAN_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_BLOCKQUOTE_ALERT_RE = re.compile(
    r"^\s{0,3}>\s*\[!([A-Za-z][A-Za-z0-9_-]*)\]([+-]?)(?:[ \t]+(\S.*?))?[ \t]*$"
)
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^\s{0,3}>( ?)?(.*)$")
_QIITA_NOTE_OPEN_RE = re.compile(
    r"^\s{0,3}:::note(?:\s+(info|warn|alert))?\s*$",
    re.IGNORECASE,
)
_ZENN_MESSAGE_OPEN_RE = re.compile(
    r"^\s{0,3}:::message(?:\s+(alert))?\s*$",
    re.IGNORECASE,
)
_DETAILS_OPEN_RE = re.compile(
    r"^\s{0,3}:::details(?:[ \t]+(.*))?\s*$",
    re.IGNORECASE,
)
_COLON_CONTAINER_CLOSE_RE = re.compile(r"^\s{0,3}:::\s*$")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*)$")
_FOOTNOTE_CONT_RE = re.compile(r"^(?: {2,}|\t)(.*)$")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")
_LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+")
_TASK_ITEM_RE = re.compile(r"^\[([ xX])\](?:[ \t]+(.*))?$")
_STRIKETHROUGH_RE = re.compile(r"~~((?:(?!~~)[^\n])+?)~~")
_IAL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_IAL_NAME_RE = re.compile(r"^[^\s.#{}]+$")
_ATTR_LIST_TOKEN_RE = re.compile(
    r"#(?P<id>[^\s.#{}]+)"
    r"|\.(?P<cls>[^\s.#{}]+)"
    r"|(?P<key>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:=(?:\"(?P<dval>[^\"]*)\"|'(?P<sval>[^']*)'|(?P<uval>[^\s{}]+)))?"
)
_STANDALONE_IAL_RE = re.compile(r"^\{:(?!:)[ \t]*(?P<inner>[^{}]*)\}\s*$")
_TRAILING_KRAMDOWN_IAL_RE = re.compile(
    r"^(?P<body>.*?)(?P<list>\{:(?!:)[ \t]*(?P<inner>[^{}]*)\})\s*$"
)
_TRAILING_PANDOC_ATTR_RE = re.compile(
    r"^(?P<body>.*?)(?P<list>\{(?![%{:])(?P<inner>[^{}]*)\})\s*$"
)
_HTML_ATTR_SKIP = frozenset({"id", "class", "style"})


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

def extract_section(
    content: str,
    heading_name: str,
    *,
    level: int | None = None,
    partial: bool = False,
) -> str:
    """Return one ATX heading section, including nested child headings.

    Matching is case-insensitive and exact by default. Set partial=True for a
    case-insensitive substring match, and level to restrict heading depth. The
    section ends at the next heading with a level less than or equal to the
    selected heading. A missing or empty heading name returns an empty string.
    """
    wanted = str(heading_name).strip()
    if not wanted:
        return ""

    selected_start: int | None = None
    selected_level: int | None = None
    lines = content.splitlines(keepends=True)

    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        current_level = len(match.group(1))
        title = match.group(2).strip()
        if level is not None and current_level != level:
            continue
        normalized_title = title.casefold()
        normalized_wanted = wanted.casefold()
        matched = (
            normalized_wanted in normalized_title
            if partial
            else normalized_title == normalized_wanted
        )
        if matched:
            selected_start = index
            selected_level = current_level
            break

    if selected_start is None or selected_level is None:
        return ""

    selected_end = len(lines)
    for index in range(selected_start + 1, len(lines)):
        match = _HEADING_RE.match(lines[index].rstrip("\r\n"))
        if match and len(match.group(1)) <= selected_level:
            selected_end = index
            break

    result = "".join(lines[selected_start:selected_end])
    if result and not result.endswith(("\n", "\r")):
        result += "\n"
    return result


def strip_prose_keep_structure(content: str) -> str:
    """Keep Markdown structure while dropping ordinary prose lines.

    Headings, lists, blockquotes, thematic breaks, and complete fenced code
    blocks are preserved verbatim. Lines inside a fenced block are always
    retained, including prose-looking lines. This is a deterministic
    extraction heuristic, not a summarizer.
    """
    kept: list[str] = []
    active_fence: str | None = None

    for line in content.splitlines():
        fence = _FENCE_RE.match(line)
        if active_fence is not None:
            kept.append(line)
            if fence and line.startswith(active_fence):
                active_fence = None
            continue

        if fence:
            kept.append(line)
            active_fence = fence.group(1)
            continue

        if not line.strip():
            if kept and kept[-1] != "":
                kept.append("")
            continue

        if (
            _HEADING_RE.match(line)
            or _HR_RE.match(line)
            or re.match(r"^\s*(?:[-+*]\s+|\d+[.)]\s+|>)", line)
        ):
            kept.append(line)

    while kept and kept[-1] == "":
        kept.pop()

    if not kept:
        return ""
    result = "\n".join(kept)
    return result + ("\n" if content.endswith(("\n", "\r")) else "")


def minify_markdown(content: str, strip_html: bool = True) -> str:
    """Apply conservative physical Markdown minification.

    When strip_html is true, HTML comments are removed. Three or more
    consecutive newlines are reduced to two, and surrounding whitespace is
    stripped. No Markdown parsing or semantic conversion is performed.
    """
    if strip_html:
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def safe_truncate(content: str, max_chars: int) -> str:
    """Truncate to max_chars while closing an open fenced code block.

    The length limit is hard: when there is enough room, a matching closing
    fence is appended within the limit. If the limit is too small to retain
    both the prefix and a closing fence, the raw prefix is returned.
    ValueError is raised for a negative limit.
    """
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    if len(content) <= max_chars:
        return content

    prefix = content[:max_chars]
    active_fence: str | None = None
    for line in prefix.splitlines():
        match = _FENCE_RE.match(line)
        if not match:
            continue
        marker = match.group(1)
        if active_fence is None:
            active_fence = marker
        elif line.startswith(active_fence):
            active_fence = None

    if active_fence is None:
        return prefix

    suffix = ("" if prefix.endswith(("\n", "\r")) else "\n") + active_fence
    if len(suffix) > max_chars:
        return prefix
    return prefix[: max_chars - len(suffix)] + suffix


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
    """Wrap ``text`` in ``~~strikethrough~~`` markers.

    ``markdown_to_html()`` turns this into ``<del>``. Unmatched ``~~``
    stays literal; a run like ``~~a~~b~~`` takes the first pair.
    """
    return f"~~{text}~~"


def blockquote(text: str) -> str:
    """Prefix every line of ``text`` with ``> `` to form a blockquote.

    ``markdown_to_html()`` turns this into ``<blockquote>``. Blank lines
    become blank ``>`` lines and split paragraphs. A ``> [!TYPE]`` opener
    is an alert, not a quote.
    """
    lines = str(text).splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines) + "\n"


def _normalize_alert_flavor(flavor: str) -> str:
    name = str(flavor).strip().lower()
    if name not in ALERT_FLAVORS:
        raise ValueError(
            f"unknown alert flavor {flavor!r}; expected one of {ALERT_FLAVORS}"
        )
    return name


def _colon_container(open_line: str, text: str) -> str:
    body = str(text)
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{open_line}\n{body}:::\n"


def _blockquote_alert_markdown(marker: str, text: str) -> str:
    body = str(text)
    if not body:
        return marker + "\n"
    return marker + "\n" + blockquote(body)


def alert(
    kind: str,
    text: str = "",
    *,
    flavor: str = "github",
    title: str | None = None,
    fold: str | None = None,
) -> str:
    """Build a site-flavored Markdown alert / callout / note block.

    ``flavor`` selects the syntax (default GitHub). ``kind`` is
    case-insensitive for the fixed vocabularies; unknown kinds raise
    ``ValueError`` except Obsidian, which allows custom type identifiers
    (Obsidian itself falls unknown types back to ``note`` visually).

    GitHub (docs.github.com, Alerts)::

        > [!NOTE]
        > Useful information that users should know

    Kinds: ``NOTE``, ``TIP``, ``IMPORTANT``, ``WARNING``, ``CAUTION``.
    Emitted uppercase. Same-line titles and fold markers are not part of
    GitHub's syntax and raise ``ValueError``.

    Qiita (qiita.com/Qiita/items/c686397e4a0f4f11683d, Note)::

        :::note info
        インフォメーション
        :::

    Kinds: ``info`` (also ``note``), ``warn``, ``alert``. ``info`` is
    optional in Qiita's parser; this helper emits ``:::note info`` for
    the info kind so the type is explicit.

    Zenn (zenn.dev/zenn/articles/markdown-guide, メッセージ)::

        :::message
        メッセージをここに
        :::

        :::message alert
        警告メッセージをここに
        :::

    Kinds: ``message`` (also ``info`` / empty) or ``alert``.

    Obsidian (help.obsidian.md/callouts)::

        > [!note]
        > body

        > [!warning] Custom title
        > body

    Kind identifiers are emitted lowercase. Optional ``title`` is written
    on the marker line. Optional ``fold`` is ``+`` (expanded) or ``-``
    (collapsed), immediately after ``[!type]``. Custom types matching
    ``[A-Za-z][A-Za-z0-9_-]*`` are allowed.

    GitLab (docs.gitlab.com, GLFM Alerts) is generation-only: the same
    five kinds as GitHub, emitted lowercase, with optional ``title``.
    GitLab's ``>>>`` multiline blockquote alerts are not generated.
    Parsed title-less lowercase five-kind markers are classified as
    Obsidian (see ``markdown_to_html``); uppercase title-less five-kind
    markers are classified as GitHub.

    Multi-line ``text``: GitHub/Obsidian/GitLab prefix each line with
    ``> `` (blank lines become ``>``); Qiita/Zenn keep the body verbatim
    between the opening and closing ``:::``.
    """
    flavor_name = _normalize_alert_flavor(flavor)
    if fold is not None and fold not in {"+", "-"}:
        raise ValueError("fold must be '+' or '-' when set")
    if title is not None:
        title = str(title)
    kind_raw = str(kind).strip()

    if flavor_name == "github":
        if title is not None:
            raise ValueError("GitHub alerts do not support a same-line title")
        if fold is not None:
            raise ValueError("GitHub alerts do not support Obsidian fold markers")
        canonical = kind_raw.upper()
        if canonical not in _GITHUB_ALERT_KIND_SET:
            raise ValueError(
                f"unknown GitHub alert kind {kind!r}; "
                f"expected one of {GITHUB_ALERT_KINDS}"
            )
        return _blockquote_alert_markdown(f"> [!{canonical}]", text)

    if flavor_name == "gitlab":
        if fold is not None:
            raise ValueError("GitLab alerts do not support Obsidian fold markers")
        canonical = kind_raw.upper()
        if canonical not in _GITHUB_ALERT_KIND_SET:
            raise ValueError(
                f"unknown GitLab alert kind {kind!r}; "
                f"expected one of {GITHUB_ALERT_KINDS}"
            )
        marker = f"> [!{canonical.lower()}]"
        if title:
            marker += f" {title}"
        return _blockquote_alert_markdown(marker, text)

    if flavor_name == "qiita":
        if title is not None or fold is not None:
            raise ValueError("Qiita notes do not support title= or fold=")
        qiita_kind = kind_raw.lower()
        if qiita_kind in {"", "note"}:
            qiita_kind = "info"
        if qiita_kind not in _QIITA_NOTE_KIND_SET:
            raise ValueError(
                f"unknown Qiita note kind {kind!r}; "
                f"expected one of {QIITA_NOTE_KINDS}"
            )
        return _colon_container(f":::note {qiita_kind}", text)

    if flavor_name == "zenn":
        if title is not None or fold is not None:
            raise ValueError("Zenn messages do not support title= or fold=")
        zenn_kind = kind_raw.lower()
        if zenn_kind in {"", "message", "info"}:
            return _colon_container(":::message", text)
        if zenn_kind == "alert":
            return _colon_container(":::message alert", text)
        raise ValueError(
            f"unknown Zenn message kind {kind!r}; "
            f"expected one of {ZENN_MESSAGE_KINDS}"
        )

    # obsidian
    if not _OBSIDIAN_KIND_RE.fullmatch(kind_raw):
        raise ValueError(
            f"invalid Obsidian callout type {kind!r}; "
            "expected a letter followed by letters, digits, '_' or '-'"
        )
    fold_mark = fold or ""
    marker = f"> [!{kind_raw.lower()}]{fold_mark}"
    if title:
        marker += f" {title}"
    return _blockquote_alert_markdown(marker, text)


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


def task_item(text: str = "", *, checked: bool = False) -> str:
    """Build one GFM task-list line (``- [ ] text`` / ``- [x] text``).

    ``markdown_to_html()`` turns this into a ``<ul>`` item with a
    disabled checkbox (``checked`` when ``checked`` is true). The box
    is not interactive.
    """
    mark = "x" if checked else " "
    body = str(text)
    if body:
        return f"- [{mark}] {body}\n"
    return f"- [{mark}]\n"


def task_list(items: Any) -> str:
    """Build a GFM task list from strings or ``(text, checked)`` pairs.

    A bare string is an unchecked item. Empty ``items`` yields ``""``.
    Mixed task and ordinary bullets in one source list stay one ``<ul>``
    with mixed ``<li>`` shapes (see ``markdown_to_html``).
    """
    lines: list[str] = []
    for item in items:
        if isinstance(item, (tuple, list)):
            if len(item) >= 2:
                text, checked = item[0], bool(item[1])
            elif len(item) == 1:
                text, checked = item[0], False
            else:
                text, checked = "", False
            lines.append(task_item(text, checked=checked).rstrip("\n"))
        else:
            lines.append(task_item(item, checked=False).rstrip("\n"))
    return "\n".join(lines) + ("\n" if lines else "")


def details(summary: str, body: str = "") -> str:
    """Build a collapsible section using Zenn's ``:::details`` form.

    ``markdown_to_html()`` turns this into ``<details><summary>…</summary>…``.
    Summary and body inlines (bold / italic / code / links) are parsed; nested
    block constructs inside the body stay paragraph text (small contract).
    Raw HTML ``<details>`` in Markdown input stays escaped — this helper is
    the supported way to emit the element.

    Nested ``:::details`` / ``::::details`` is unsupported: the first ``:::``
    closer ends the block.
    """
    summary_line = str(summary).replace("\n", " ").strip()
    opener = f":::details {summary_line}" if summary_line else ":::details"
    body_text = str(body)
    if not body_text.strip():
        return f"{opener}\n:::\n"
    if not body_text.endswith("\n"):
        body_text += "\n"
    return f"{opener}\n{body_text}:::\n"


def footnote_ref(ident: str) -> str:
    """Build an inline footnote reference (``[^id]``).

    ``markdown_to_html()`` turns a defined ref into a superscript link.
    Undefined ids stay literal (documented degrade).
    """
    return f"[^{ident}]"


def footnote(ident: str, text: str = "") -> str:
    """Build a footnote definition line (``[^id]: text``).

    Pair with :func:`footnote_ref` (or a hand-written ``[^id]``) in the
    body. Duplicate ids: first definition wins. Unused definitions are
    dropped from HTML. Continuation lines may be indented with 2+ spaces
    or a tab; unindented wrapping is a new paragraph, not a continuation.
    """
    return f"[^{ident}]: {text}\n"


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

    ``markdown_to_html()`` parses this pipe-table form into
    ``<table>/<thead>/<th>/<tbody>/<td>``.

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
    """Join a heading with a sequence of pre-rendered Markdown blocks.

    Every builder in this module (``bullet_list``, ``table``, ``code_block``,
    ...) already ends its output in a trailing newline, so back-to-back
    blocks never need anything inserted between them. A plain string used
    directly as a block -- typically hand-written prose with no trailing
    newline of its own -- is the one case that doesn't: joined with nothing
    in between, its text runs directly onto the next block's own leading
    syntax (``- ``, ``# ``, a fence), so e.g. a following bullet list's
    first item silently becomes part of the previous line's plain text
    instead of a list. A newline is inserted only when the accumulated
    output doesn't already end in one, so well-formed blocks are untouched.
    """
    body = heading(title, level=level)
    for block in blocks:
        block = str(block)
        if not block:
            continue
        if not body.endswith("\n"):
            body += "\n"
        body += block
    return body


def wrap_section(name: str, content: str) -> str:
    """Wrap ``content`` in HTML-comment markers so tools/LLMs can find section boundaries."""
    content = content if content.endswith("\n") else content + "\n"
    return f"<!-- BEGIN_SECTION:{name} -->\n{content}<!-- END_SECTION:{name} -->\n"


# ---------------------------------------------------------------------------
# Kramdown IAL (thin heading/paragraph subset — not full Kramdown)
# ---------------------------------------------------------------------------

def _escape_ial_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _parse_attr_list_inner(inner: str) -> dict[str, Any] | None:
    """Parse ``#id .class key=value`` tokens. ``None`` if leftover junk remains."""
    text = inner.strip()
    parsed: dict[str, Any] = {
        "id": None,
        "classes": [],
        "attrs": {},
        "flags": [],
    }
    if not text:
        return parsed
    pos = 0
    length = len(text)
    while pos < length:
        if text[pos].isspace():
            pos += 1
            continue
        match = _ATTR_LIST_TOKEN_RE.match(text, pos)
        if not match:
            return None
        if match.group("id"):
            parsed["id"] = match.group("id")
        elif match.group("cls"):
            parsed["classes"].append(match.group("cls"))
        elif match.group("key"):
            key = match.group("key")
            if match.group("dval") is not None:
                parsed["attrs"][key] = match.group("dval")
            elif match.group("sval") is not None:
                parsed["attrs"][key] = match.group("sval")
            elif match.group("uval") is not None:
                parsed["attrs"][key] = match.group("uval")
            else:
                parsed["flags"].append(key)
        pos = match.end()
    return parsed


def _is_toc_only(parsed: dict[str, Any]) -> bool:
    if parsed["id"] or parsed["classes"] or parsed["attrs"]:
        return False
    flags = [flag.lower() for flag in parsed["flags"]]
    return flags == ["toc"]


def _format_attr_tokens(
    ident: str | None,
    classes: list[str],
    attrs: dict[str, str],
    flags: list[str] | None = None,
) -> list[str]:
    parts: list[str] = []
    if ident:
        parts.append("#" + ident)
    for item in classes:
        parts.append("." + item)
    for key, value in attrs.items():
        parts.append(f'{key}="{_escape_ial_value(value)}"')
    for flag in flags or []:
        parts.append(flag)
    return parts


def _format_ial_line(
    ident: str | None,
    classes: list[str],
    attrs: dict[str, str],
    flags: list[str] | None = None,
) -> str:
    tokens = _format_attr_tokens(ident, classes, attrs, flags)
    if not tokens:
        return "{:}"
    return "{: " + " ".join(tokens) + "}"


def _format_pandoc_attr_list(
    ident: str | None,
    classes: list[str],
    attrs: dict[str, str],
) -> str:
    tokens = _format_attr_tokens(ident, classes, attrs)
    return "{" + " ".join(tokens) + "}"


def _html_attrs_to_pandoc_suffix(attr: dict[str, str]) -> str:
    ident = (attr.get("id") or "").strip()
    if ident and not _IAL_NAME_RE.fullmatch(ident):
        ident = ""
    classes: list[str] = []
    for item in (attr.get("class") or "").split():
        item = item.strip()
        if item and _IAL_NAME_RE.fullmatch(item):
            classes.append(item)
    extra: dict[str, str] = {}
    for key, value in attr.items():
        if key in _HTML_ATTR_SKIP or not value:
            continue
        if not _IAL_KEY_RE.fullmatch(key):
            continue
        extra[key] = value
    if not ident and not classes and not extra:
        return ""
    return " " + _format_pandoc_attr_list(ident or None, classes, extra)


def _normalize_ial_id(ident: str | None) -> str | None:
    if ident is None:
        return None
    ident = str(ident).strip().lstrip("#")
    if not ident:
        return None
    if not _IAL_NAME_RE.fullmatch(ident):
        raise ValueError(f"invalid IAL id {ident!r}")
    return ident


def _normalize_ial_classes(classes: Any) -> list[str]:
    if classes is None:
        return []
    if isinstance(classes, str):
        items = classes.split()
    else:
        items = [str(item) for item in classes]
    out: list[str] = []
    for item in items:
        item = item.strip().lstrip(".")
        if not item:
            continue
        if not _IAL_NAME_RE.fullmatch(item):
            raise ValueError(f"invalid IAL class {item!r}")
        out.append(item)
    return out


def _normalize_ial_attrs(attrs: dict[str, Any]) -> dict[str, str]:
    extra: dict[str, str] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        if not _IAL_KEY_RE.fullmatch(str(key)):
            raise ValueError(f"invalid IAL key {key!r}")
        extra[str(key)] = str(value)
    return extra


def ial(
    *,
    id: str | None = None,
    classes: Any = None,
    **attrs: Any,
) -> str:
    """Build a Kramdown Inline Attribute List line.

    Emits ``{: #id .class key="value"}\\n``. Empty input (no id, classes, or
    attrs) returns ``""``. ``classes`` may be a string (whitespace-split) or
    a sequence. A leading ``#`` / ``.`` on id / class names is stripped.

    This is a generator only — ``markdown_to_html`` does not apply IAL as
    HTML attributes.

    >>> ial(id="intro", classes="hero")
    '{: #intro .hero}\\n'
    >>> ial(id="box", classes=["note", "wide"], role="note")
    '{: #box .note .wide role="note"}\\n'
    """
    ident = _normalize_ial_id(id)
    class_list = _normalize_ial_classes(classes)
    extra = _normalize_ial_attrs(attrs)
    if not ident and not class_list and not extra:
        return ""
    return _format_ial_line(ident, class_list, extra) + "\n"


def with_attributes(
    block_md: str,
    *,
    id: str | None = None,
    classes: Any = None,
    **attrs: Any,
) -> str:
    """Append a block IAL after ``block_md``.

    Intended for headings and paragraphs (tiny contract). ``block_md`` is
    left unchanged when no attributes are given.

    >>> with_attributes(heading("Title"), id="intro", classes="hero")
    '# Title\\n{: #intro .hero}\\n'
    """
    suffix = ial(id=id, classes=classes, **attrs)
    if not suffix:
        return str(block_md)
    body = str(block_md)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + suffix


def _join_converted_lines(lines: list[str], original: str) -> str:
    if not lines:
        return "\n" if original.endswith("\n") else ""
    text = "\n".join(lines)
    if original.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    return text


def _is_heading_or_paragraph_line(line: str) -> bool:
    """True when a trailing attr list may attach (headings / paragraphs only)."""
    if _HEADING_RE.match(line):
        return True
    stripped = line.strip()
    if not stripped:
        return False
    if _HR_RE.match(line) or _FENCE_RE.match(line):
        return False
    if _LIST_ITEM_RE.match(line):
        return False
    if line.lstrip().startswith(">"):
        return False
    if _FOOTNOTE_DEF_RE.match(line):
        return False
    if _details_open_summary(line) is not None:
        return False
    if _COLON_CONTAINER_CLOSE_RE.match(line):
        return False
    if _qiita_note_kind(line) is not None or _zenn_message_kind(line) is not None:
        return False
    if stripped.startswith("|"):
        return False
    if stripped.startswith("<!--") or stripped.startswith("{%"):
        return False
    if stripped.startswith("{{"):
        return False
    if stripped == "---":
        return False
    return True


def _rewrite_trailing_attr_list(line: str) -> tuple[str, str] | None:
    """Peel a trailing Pandoc or inline IAL list into ``(body, ial_line)``."""
    if not _is_heading_or_paragraph_line(line):
        return None
    ial_match = _TRAILING_KRAMDOWN_IAL_RE.match(line)
    if ial_match and ial_match.group("body").strip():
        parsed = _parse_attr_list_inner(ial_match.group("inner"))
        if parsed is None or _is_toc_only(parsed):
            return None
        body = ial_match.group("body").rstrip()
        return body, _format_ial_line(
            parsed["id"], parsed["classes"], parsed["attrs"], parsed["flags"]
        )
    pandoc_match = _TRAILING_PANDOC_ATTR_RE.match(line)
    if pandoc_match and pandoc_match.group("body").strip():
        parsed = _parse_attr_list_inner(pandoc_match.group("inner"))
        if parsed is None or _is_toc_only(parsed):
            return None
        if not (
            parsed["id"]
            or parsed["classes"]
            or parsed["attrs"]
            or parsed["flags"]
        ):
            return None
        body = pandoc_match.group("body").rstrip()
        return body, _format_ial_line(
            parsed["id"], parsed["classes"], parsed["attrs"], parsed["flags"]
        )
    return None


def markdown_to_kramdown(content: str) -> str:
    """Rewrite a Markdown subset into Kramdown-leaning Markdown.

    **Rewritten**

    - Pandoc / PHP Markdown Extra trailing braces on headings and
      paragraphs: ``# Title {#id .class key=value}`` becomes a block IAL
      on the next line (``{: #id .class key="value"}``).
    - Inline IAL already on a heading/paragraph (``# Title {: #id}``) is
      moved to a following block IAL and normalized (id, then classes,
      then ``key="value"``).
    - A standalone parseable IAL line is normalized the same way.

    **Left alone**

    - Ordinary Markdown with no attribute list (headings, lists, tables,
      fences, alerts, ``:::details``, footnotes, …).
    - Fenced code (attribute-looking braces inside stay literal).
    - ``{:toc}`` (Kramdown TOC macro), ``{::comment}`` / other extensions,
      span IAL, IAL on lists / quotes / tables, Liquid ``{% %}`` / ``{{ }}``,
      YAML front matter, and unknown brace constructs (safe degrade:
      leave-as-is).

    This is **not** a Kramdown engine. ``markdown_to_html`` does not apply
    IAL as HTML attributes.

    >>> markdown_to_kramdown("# Title {#intro .hero}").splitlines()
    ['# Title', '{: #intro .hero}']
    """
    lines = content.splitlines()
    out: list[str] = []
    in_fence: str | None = None
    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if in_fence is None:
                in_fence = mark
            elif line.startswith(in_fence):
                in_fence = None
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        standalone = _STANDALONE_IAL_RE.match(line)
        if standalone:
            parsed = _parse_attr_list_inner(standalone.group("inner"))
            if parsed is None or _is_toc_only(parsed):
                out.append(line)
            else:
                out.append(
                    _format_ial_line(
                        parsed["id"],
                        parsed["classes"],
                        parsed["attrs"],
                        parsed["flags"],
                    )
                )
            continue
        rewritten = _rewrite_trailing_attr_list(line)
        if rewritten is not None:
            body, ial_line = rewritten
            out.append(body)
            out.append(ial_line)
            continue
        out.append(line)
    return _join_converted_lines(out, content)


def kramdown_to_markdown(content: str) -> str:
    """Strip the supported IAL subset back toward plain Markdown.

    **Rewritten (lossy)**

    - A standalone IAL line ``{: #id .class key="value"}`` after a block
      is removed. Attributes are dropped.
    - A trailing inline IAL on a heading or paragraph
      (``# Title {: #id}``) is stripped.

    **Kept / left alone**

    - The heading or paragraph text itself.
    - Ordinary Markdown, including Pandoc-style ``{#id}`` that was never
      converted to IAL.
    - ``{:toc}``, ``{::comment}`` / ``{::options}`` / ``{::nomarkdown}``,
      unparseable IAL, Liquid ``{% %}`` / ``{{ }}``, YAML front matter,
      and fenced code. Standalone parseable IAL is stripped wherever it
      appears (the line does not record which block it attached to).

    Round-trip ``markdown_to_kramdown`` → ``kramdown_to_markdown`` keeps
    prose and structure; attribute lists are lost. That is intentional.

    >>> kramdown_to_markdown("# Title\\n{: #intro .hero}\\n").splitlines()
    ['# Title']
    """
    lines = content.splitlines()
    out: list[str] = []
    in_fence: str | None = None
    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if in_fence is None:
                in_fence = mark
            elif line.startswith(in_fence):
                in_fence = None
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        standalone = _STANDALONE_IAL_RE.match(line)
        if standalone:
            parsed = _parse_attr_list_inner(standalone.group("inner"))
            if parsed is None or _is_toc_only(parsed):
                out.append(line)
            continue
        ial_match = _TRAILING_KRAMDOWN_IAL_RE.match(line)
        if ial_match:
            body = ial_match.group("body").rstrip()
            parsed = _parse_attr_list_inner(ial_match.group("inner"))
            if (
                parsed is not None
                and not _is_toc_only(parsed)
                and _is_heading_or_paragraph_line(body)
            ):
                out.append(body)
                continue
        out.append(line)
    return _join_converted_lines(out, content)


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
        self._table_depth = 0
        self._table_header: list[str] | None = None
        self._table_rows: list[list[str]] = []
        self._table_row: list[str] | None = None
        self._table_cell: list[str] | None = None
        self._table_row_is_header = False
        self._in_thead = False
        self._block_attr_suffixes: list[str] = []

    def _emit(self, text: str) -> None:
        if self._table_cell is not None:
            self._table_cell.append(text)
        elif self._table_depth:
            return
        else:
            self.parts.append(text)

    def _flush_cell(self) -> None:
        if self._table_cell is None or self._table_row is None:
            self._table_cell = None
            return
        text = re.sub(r"\s+", " ", "".join(self._table_cell)).strip()
        text = text.replace("|", r"\|")
        self._table_row.append(text)
        self._table_cell = None

    def _flush_row(self) -> None:
        self._flush_cell()
        if self._table_row is None:
            return
        if self._in_thead or self._table_row_is_header:
            if self._table_header is None:
                self._table_header = self._table_row
            else:
                self._table_rows.append(self._table_row)
        else:
            self._table_rows.append(self._table_row)
        self._table_row = None
        self._table_row_is_header = False

    def _table_to_markdown(self) -> str:
        self._flush_row()
        rows = self._table_rows
        header = self._table_header
        if header is None:
            if not rows:
                return ""
            width = max(len(row) for row in rows)
            if width == 0:
                return ""
            header = [""] * width
        width = len(header)
        if width == 0:
            return ""

        def fmt(cells: list[str]) -> str:
            padded = list(cells[:width]) + [""] * (width - min(len(cells), width))
            return "| " + " | ".join(padded) + " |"

        lines = [fmt(header), "| " + " | ".join(["---"] * width) + " |"]
        for row in rows:
            lines.append(fmt(row))
        return "\n\n" + "\n".join(lines) + "\n\n"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in {"script", "style"}:
            self._suppress += 1
            return
        if self._suppress:
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_header = None
                self._table_rows = []
                self._table_row = None
                self._table_cell = None
                self._table_row_is_header = False
                self._in_thead = False
            return
        if self._table_depth > 1 and tag in {
            "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
        }:
            return
        if tag == "thead":
            if self._table_depth == 1:
                self._in_thead = True
            return
        if tag == "tr":
            if self._table_depth == 1:
                self._flush_row()
                self._table_row = []
                self._table_row_is_header = False
            return
        if tag in {"td", "th"}:
            if self._table_depth != 1:
                return
            if self._table_row is None:
                self._table_row = []
                self._table_row_is_header = False
            self._flush_cell()
            self._table_cell = []
            if tag == "th":
                self._table_row_is_header = True
            return
        if tag in {"tbody", "tfoot", "caption", "colgroup", "col"}:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._emit("\n\n" + ("#" * level) + " ")
            self._block_attr_suffixes.append(_html_attrs_to_pandoc_suffix(attr))
        elif tag == "p":
            self._emit("\n\n")
            self._block_attr_suffixes.append(_html_attrs_to_pandoc_suffix(attr))
        elif tag == "br":
            self._emit("  \n")
        elif tag == "hr":
            self._emit("\n\n---\n\n")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag in {"del", "s"}:
            self._emit("~~")
        elif tag == "code" and not self._in_pre:
            self._emit("`")
            self._in_code = True
        elif tag == "pre":
            self._in_pre = True
            self._emit("\n\n```\n")
        elif tag == "a":
            self._emit("[")
            self._link_href = attr.get("href", "")
            self._link_title = attr.get("title", "")
            self._link_open = True
        elif tag == "img":
            self._emit(
                make_image(
                    attr.get("alt", ""),
                    attr.get("src", ""),
                    attr.get("title") or None,
                )
            )
        elif tag == "input":
            if attr.get("type", "").lower() == "checkbox":
                mark = "x" if "checked" in attr else " "
                self._emit(f"[{mark}]")
        elif tag in {"ul", "ol"}:
            self._list_stack.append(tag)
            self._li_index.append(0)
            self._emit("\n")
        elif tag == "li":
            depth = max(len(self._list_stack) - 1, 0)
            indent = "  " * depth
            kind = self._list_stack[-1] if self._list_stack else "ul"
            if kind == "ol":
                self._li_index[-1] += 1
                bullet = f"{self._li_index[-1]}."
            else:
                bullet = "-"
            self._emit(f"\n{indent}{bullet} ")
        elif tag == "blockquote":
            self._emit("\n\n> ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress:
            return
        if tag == "table":
            if self._table_depth == 1:
                markdown = self._table_to_markdown()
                self._table_depth = 0
                self._table_cell = None
                self._table_row = None
                if markdown:
                    self.parts.append(markdown)
            elif self._table_depth > 1:
                self._table_depth -= 1
            return
        if self._table_depth > 1 and tag in {
            "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
        }:
            return
        if tag == "thead":
            if self._table_depth == 1:
                self._flush_row()
                self._in_thead = False
            return
        if tag == "tr":
            if self._table_depth == 1:
                self._flush_row()
            return
        if tag in {"td", "th"}:
            if self._table_depth == 1:
                self._flush_cell()
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            suffix = (
                self._block_attr_suffixes.pop()
                if self._block_attr_suffixes
                else ""
            )
            if suffix:
                self._emit(suffix)
            self._emit("\n\n")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag in {"del", "s"}:
            self._emit("~~")
        elif tag == "code" and not self._in_pre:
            self._emit("`")
            self._in_code = False
        elif tag == "pre":
            self._emit("\n```\n\n")
            self._in_pre = False
        elif tag == "a" and self._link_open:
            if self._link_title:
                self._emit(f']({self._link_href} "{self._link_title}")')
            else:
                self._emit(f"]({self._link_href})")
            self._link_open = False
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            if self._li_index:
                self._li_index.pop()
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        if self._table_depth and self._table_cell is None:
            return
        if self._in_pre or self._in_code:
            self._emit(data)
        else:
            self._emit(re.sub(r"\s+", " ", data))

    def output(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def html_to_markdown(html: str) -> str:
    """Conservatively convert common HTML tags to Markdown.

    Simple ``<table>`` trees become GFM pipe tables (``| a | b |`` plus a
    ``| --- |`` separator). ``<thead>`` / ``<th>`` rows are headers;
    headerless tables use an empty header row so every ``<td>`` row stays
    data (GFM cannot represent a table with no header). ``|`` inside cells
    is escaped as ``\\|``. Nested tables flatten to cell text; ``colspan`` /
    ``rowspan`` and ``<caption>`` are ignored.

    ``<del>`` (and ``<s>``) become ``~~text~~``. A checkbox
    ``<input type="checkbox">`` (optional ``checked``) becomes ``[ ]`` /
    ``[x]``, which pairs with ``<li>`` as ``- [ ]`` / ``- [x]``.

    ``id`` / ``class`` (and other simple key=value attrs except ``style``)
    on ``<h1>``–``<h6>`` and ``<p>`` become a trailing Pandoc-style
    ``{#id .class key="value"}`` so :func:`markdown_to_kramdown` can attach
    a Kramdown IAL. This is not applied to tables, lists, or other tags.
    """
    parser = _HTMLToMarkdownParser()
    parser.feed(html)
    parser.close()
    return parser.output()


def _escape_html_text(text: str) -> str:
    return html_module.escape(text, quote=False)


def _angle_autolink_html(url: str) -> str:
    """Render a stashed ``<http(s)://...>`` autolink as ``<a href>``."""
    href = html_module.escape(url, quote=True)
    text = html_module.escape(url)
    return f'<a href="{href}">{text}</a>'


def _parse_task_item(item: str) -> tuple[bool, str] | None:
    """Return ``(checked, rest)`` for a GFM task marker, else ``None``.

    Only the item body is inspected (list marker already stripped).
    ``[ ]`` is unchecked; ``[x]`` / ``[X]`` are checked. A following
    description needs whitespace after ``]``; ``- [x]done`` is not a task.
    """
    match = _TASK_ITEM_RE.match(item)
    if not match:
        return None
    checked = match.group(1) in "xX"
    rest = match.group(2) or ""
    return checked, rest


def _task_checkbox_html(checked: bool) -> str:
    if checked:
        return '<input type="checkbox" disabled checked />'
    return '<input type="checkbox" disabled />'


def _unquote_blockquote_line(line: str) -> str | None:
    match = _BLOCKQUOTE_PREFIX_RE.match(line)
    if not match:
        return None
    return match.group(2)


def _blockquote_alert_info(line: str) -> dict[str, str] | None:
    match = _BLOCKQUOTE_ALERT_RE.match(line)
    if not match:
        return None
    raw_kind = match.group(1)
    fold = match.group(2) or ""
    title = (match.group(3) or "").strip()
    github_shaped = (
        raw_kind in _GITHUB_ALERT_KIND_SET and not fold and not title
    )
    if github_shaped:
        return {
            "flavor": "github",
            "kind": raw_kind,
            "title": raw_kind,
            "fold": "",
        }
    return {
        "flavor": "obsidian",
        "kind": raw_kind.lower(),
        "title": title,
        "fold": fold,
    }


def _qiita_note_kind(line: str) -> str | None:
    match = _QIITA_NOTE_OPEN_RE.match(line)
    if not match:
        return None
    return (match.group(1) or "info").lower()


def _zenn_message_kind(line: str) -> str | None:
    match = _ZENN_MESSAGE_OPEN_RE.match(line)
    if not match:
        return None
    return (match.group(1) or "message").lower()


def _is_alert_block_open(line: str) -> bool:
    return bool(
        _blockquote_alert_info(line)
        or _qiita_note_kind(line) is not None
        or _zenn_message_kind(line) is not None
        or _details_open_summary(line) is not None
    )


def _details_open_summary(line: str) -> str | None:
    """Return the summary text if ``line`` opens a ``:::details`` block."""
    match = _DETAILS_OPEN_RE.match(line)
    if not match:
        return None
    return (match.group(1) or "").strip()


def _footnote_slug(ident: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", ident).strip("-")
    return slug or "note"


def _collect_footnote_definitions(lines: list[str]) -> tuple[list[str], dict[str, str]]:
    """Strip ``[^id]:`` definitions (except inside fences); first id wins.

    Indented (2+ spaces or a tab) lines immediately after a definition are
    continuations. A blank line or a non-indented line ends the note.
    """
    defs: dict[str, str] = {}
    kept: list[str] = []
    i = 0
    in_fence: str | None = None
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if in_fence is None:
                in_fence = mark
            elif line.startswith(in_fence):
                in_fence = None
            kept.append(line)
            i += 1
            continue
        if in_fence:
            kept.append(line)
            i += 1
            continue
        match = _FOOTNOTE_DEF_RE.match(line)
        if match:
            ident = match.group(1)
            parts = [match.group(2)]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    break
                if _FOOTNOTE_DEF_RE.match(nxt):
                    break
                cont = _FOOTNOTE_CONT_RE.match(nxt)
                if not cont:
                    break
                parts.append(cont.group(1).strip())
                i += 1
            if ident not in defs:
                defs[ident] = " ".join(p for p in parts if p).strip()
            continue
        kept.append(line)
        i += 1
    return kept, defs


def _footnote_ref_html(ident: str, number: int, *, with_id: bool) -> str:
    slug = html_module.escape(_footnote_slug(ident), quote=True)
    label = html_module.escape(str(number))
    if with_id:
        return (
            f'<sup class="footnote-ref">'
            f'<a href="#fn-{slug}" id="fnref-{slug}">{label}</a>'
            f"</sup>"
        )
    return (
        f'<sup class="footnote-ref">'
        f'<a href="#fn-{slug}">{label}</a>'
        f"</sup>"
    )


def _footnotes_section_html(
    order: list[str],
    defs: dict[str, str],
    render_inline,
) -> str:
    items: list[str] = ['<section class="footnotes">', "<ol>"]
    for ident in order:
        slug = html_module.escape(_footnote_slug(ident), quote=True)
        note = render_inline(defs.get(ident, ""))
        items.append(
            f'<li id="fn-{slug}">{note}'
            f' <a href="#fnref-{slug}" class="footnote-backref">↩</a></li>'
        )
    items.append("</ol>")
    items.append("</section>")
    return "\n".join(items)


def _consume_details(
    lines: list[str],
    start: int,
    summary: str,
    render_inline,
) -> tuple[str, int]:
    """Collect a ``:::details`` block into ``<details><summary>``.

    Body lines become paragraphs (blank lines split them). Inner ATX /
    lists / nested ``:::details`` stay paragraph text — the first ``:::``
    closer ends the block.
    """
    i = start + 1
    body_lines: list[str] = []
    while i < len(lines) and not _COLON_CONTAINER_CLOSE_RE.match(lines[i]):
        body_lines.append(lines[i])
        i += 1
    if i < len(lines) and _COLON_CONTAINER_CLOSE_RE.match(lines[i]):
        i += 1

    paragraphs: list[list[str]] = [[]]
    for raw in body_lines:
        if not raw.strip():
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(raw)

    parts = ["<details>", f"<summary>{render_inline(summary)}</summary>"]
    for para in paragraphs:
        if not para:
            continue
        text = " ".join(s.strip() for s in para)
        parts.append(f"<p>{render_inline(text)}</p>")
    parts.append("</details>")
    return "\n".join(parts), i


def _alert_aside_html(
    *,
    flavor: str,
    kind: str,
    title: str,
    body_markdown: str,
    fold: str = "",
    render_inline,
) -> str:
    kind_slug = kind.lower()
    kind_token = html_module.escape(kind.upper(), quote=True)
    flavor_token = html_module.escape(flavor, quote=True)
    css_kind = html_module.escape(kind_slug, quote=True)
    attrs = [
        f'class="markdown-alert markdown-alert-{css_kind}"',
        f'data-alert="{kind_token}"',
        f'data-alert-flavor="{flavor_token}"',
    ]
    if fold == "+":
        attrs.append('data-alert-fold="open"')
    elif fold == "-":
        attrs.append('data-alert-fold="closed"')
    display_title = title if title else kind.upper()
    parts = [
        f"<aside {' '.join(attrs)}>",
        f'<p class="markdown-alert-title">{render_inline(display_title)}</p>',
    ]
    body = body_markdown.strip("\n")
    if body.strip():
        body_html = markdown_to_html(body).rstrip("\n")
        if body_html:
            parts.append(body_html)
    parts.append("</aside>")
    return "\n".join(parts)


def _consume_blockquote_alert(
    lines: list[str],
    start: int,
    info: dict[str, str],
    render_inline,
) -> tuple[str, int]:
    i = start + 1
    body_lines: list[str] = []
    while i < len(lines):
        unquoted = _unquote_blockquote_line(lines[i])
        if unquoted is None:
            break
        body_lines.append(unquoted)
        i += 1
    html = _alert_aside_html(
        flavor=info["flavor"],
        kind=info["kind"],
        title=info["title"],
        body_markdown="\n".join(body_lines),
        fold=info["fold"],
        render_inline=render_inline,
    )
    return html, i


def _consume_colon_alert(
    lines: list[str],
    start: int,
    *,
    flavor: str,
    kind: str,
    render_inline,
) -> tuple[str, int]:
    i = start + 1
    body_lines: list[str] = []
    while i < len(lines) and not _COLON_CONTAINER_CLOSE_RE.match(lines[i]):
        body_lines.append(lines[i])
        i += 1
    if i < len(lines) and _COLON_CONTAINER_CLOSE_RE.match(lines[i]):
        i += 1
    title = kind.upper()
    html = _alert_aside_html(
        flavor=flavor,
        kind=kind,
        title=title,
        body_markdown="\n".join(body_lines),
        render_inline=render_inline,
    )
    return html, i


def _split_table_row(line: str) -> list[str]:
    """Split a GFM pipe row into cells.

    Leading/trailing pipes are optional. A ``\\|`` sequence stays one cell
    (the backslash is dropped). This is the same split ``table()`` output
    round-trips through; it is not a full GFM cell lexer.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", stripped)]


def _looks_like_table_row(line: str) -> bool:
    return "|" in line.strip()


def _is_table_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(_TABLE_SEP_CELL_RE.match(cell.replace(" ", "")) for cell in cells)


def _is_table_start(lines: list[str], index: int) -> bool:
    """True when ``lines[index]`` is a header row followed by a ``| --- |`` delimiter.

    List markers, headings, fences, thematic breaks, and ``>`` quotes are
    not treated as headers, so a malformed ``- a | b`` line stays a list.
    A pipe row with no delimiter after it stays a paragraph (safe degrade).
    """
    if index + 1 >= len(lines):
        return False
    header = lines[index]
    if not header.strip() or not _looks_like_table_row(header):
        return False
    if (
        _HEADING_RE.match(header)
        or _FENCE_RE.match(header)
        or _HR_RE.match(header)
        or _unquote_blockquote_line(header) is not None
        or _LIST_ITEM_RE.match(header)
        or _is_alert_block_open(header)
    ):
        return False
    return _is_table_separator(lines[index + 1])


def _table_html(
    headers: list[str],
    rows: list[list[str]],
    render_inline,
) -> str:
    width = len(headers)

    def row_html(tag: str, cells: list[str]) -> str:
        padded = list(cells[:width]) + [""] * (width - min(len(cells), width))
        inner = "".join(f"<{tag}>{render_inline(cell)}</{tag}>" for cell in padded)
        return f"<tr>{inner}</tr>"

    parts = ["<table>", "<thead>", row_html("th", headers), "</thead>", "<tbody>"]
    for row in rows:
        parts.append(row_html("td", row))
    parts.append("</tbody>")
    parts.append("</table>")
    return "\n".join(parts)


def _consume_table(
    lines: list[str],
    start: int,
    render_inline,
) -> tuple[str, int]:
    headers = _split_table_row(lines[start])
    i = start + 2
    rows: list[list[str]] = []
    while i < len(lines):
        line = lines[i]
        if not line.strip() or not _looks_like_table_row(line):
            break
        if (
            _HEADING_RE.match(line)
            or _FENCE_RE.match(line)
            or _HR_RE.match(line)
            or _unquote_blockquote_line(line) is not None
            or _is_alert_block_open(line)
        ):
            break
        rows.append(_split_table_row(line))
        i += 1
    return _table_html(headers, rows, render_inline), i


def _consume_blockquote(
    lines: list[str],
    start: int,
    render_inline,
) -> tuple[str, int]:
    """Collect consecutive ``>`` lines into a ``<blockquote>``.

    Blank ``>`` / ``> `` lines split paragraphs. Inner ATX headings, lists,
    nested quotes, and tables are **not** re-parsed as blocks -- they stay
    paragraph text -- so GitLab ``>>>`` openers cannot collapse into alerts.
    A later ``> [!TYPE]`` line ends this quote so the alert handler can win.
    """
    i = start
    body_lines: list[str] = []
    while i < len(lines):
        unquoted = _unquote_blockquote_line(lines[i])
        if unquoted is None:
            break
        if i > start and _blockquote_alert_info(lines[i]):
            break
        body_lines.append(unquoted)
        i += 1

    paragraphs: list[list[str]] = [[]]
    for raw in body_lines:
        if not raw.strip():
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(raw)

    parts = ["<blockquote>"]
    for para in paragraphs:
        if not para:
            continue
        text = " ".join(s.strip() for s in para)
        parts.append(f"<p>{render_inline(text)}</p>")
    parts.append("</blockquote>")
    return "\n".join(parts), i


def markdown_to_html(content: str) -> str:
    """Conservatively convert a Markdown *subset* to HTML.

    Handles ATX headings, fenced code, paragraphs, inline code, bold/italic,
    strikethrough (``~~text~~`` → ``<del>``), links, images, angle-bracket
    http(s) autolinks, thematic breaks, simple bullet/numbered lists, GFM
    task lists, ordinary ``>`` blockquotes, simple GFM pipe tables,
    GitHub / Qiita / Zenn / Obsidian alerts, Zenn-style ``:::details``
    collapsible sections, and a small GFM/Pandoc-like footnote subset.

    Alerts render to a shared ``<aside>`` shape. Pair with
    ``default_stylesheet()`` (or ``alert_stylesheet()``) if you want CSS::

        <style>{default_stylesheet()}</style>
        <aside class="markdown-alert markdown-alert-note"
               data-alert="NOTE" data-alert-flavor="github">
        <p class="markdown-alert-title">NOTE</p>
        <p>…body…</p>
        </aside>

    Classification:

    - ``> [!NOTE]`` (uppercase GitHub kinds, no same-line title, no fold)
      is ``data-alert-flavor="github"``.
    - Other ``> [!type]`` markers (lowercase, extra types, optional title,
      ``+``/``-`` fold) are ``obsidian``. GitLab's lowercase five-kind
      form therefore parses as Obsidian; its ``>>>`` multiline alerts are
      not recognized.
    - ``:::note`` / ``:::note info|warn|alert`` … ``:::`` is ``qiita``.
    - ``:::message`` / ``:::message alert`` … ``:::`` is ``zenn``.
    - ``:::details Summary`` … ``:::`` becomes ``<details><summary>``.
      Nested inlines in the summary and body are parsed; nested blocks
      stay paragraph text. Raw HTML ``<details>`` stays escaped.
    - Ordinary ``>`` lines that are not an alert opener become
      ``<blockquote>`` (multi-line; blank ``>`` lines split paragraphs).
      Nested block constructs inside the quote are not parsed.
    - A header row followed by a ``| --- |`` delimiter row becomes
      ``<table>``. Cell text is escaped, then the same inline renderer
      used for paragraphs is applied (bold / italic / code / links / images
      / strikethrough / angle autolinks / footnotes). Pipe rows without
      that delimiter stay paragraphs.
    - ``~~text~~`` becomes ``<del>text</del>``. Unmatched ``~~`` stays
      literal; ``~~a~~b~~`` takes the first pair. Nested bold/italic
      inside or around a pair is applied.
    - Unordered ``-`` / ``*`` / ``+`` items whose body is ``[ ]`` /
      ``[x]`` / ``[X]`` (optional description after whitespace) become
      ``<li>`` with ``<input type="checkbox" disabled>`` (``checked``
      for x/X). The box is not interactive. Task items and ordinary
      bullets in the same list stay one ``<ul>`` with mixed ``<li>``
      shapes. ``1. [ ]`` stays ordinary ordered-list text.
    - ``<https://...>`` / ``<http://...>`` become ``<a href="...">``.
      Bare URLs, ``mailto:``, and ordinary ``<tag>`` (including
      ``<script>``) are not autolinks; non-URL angle brackets stay
      escaped. ``[text](url)`` links still win when both forms appear.
    - ``[^id]`` plus a ``[^id]: note`` definition become a superscript
      link and a trailing ``<section class="footnotes">``. Undefined
      refs stay literal. Duplicate ids: first definition wins. Unused
      defs are dropped. Only indented (2+ spaces / tab) continuation
      is kept; unindented wrapping is a new paragraph.

    Unknown GitHub/Qiita/Zenn kinds are not generated (``alert()`` raises);
    unknown Obsidian types still parse as Obsidian callouts (matching
    Obsidian's custom-type support).
    """
    source_lines = content.splitlines()
    lines, footnote_defs = _collect_footnote_definitions(source_lines)
    footnote_order: list[str] = []
    footnote_ref_seen: set[str] = set()
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

        def replace_footnote(match: re.Match[str]) -> str:
            ident = match.group(1)
            if ident not in footnote_defs:
                return match.group(0)
            if ident not in footnote_order:
                footnote_order.append(ident)
            number = footnote_order.index(ident) + 1
            with_id = ident not in footnote_ref_seen
            if with_id:
                footnote_ref_seen.add(ident)
            return stash(_footnote_ref_html(ident, number, with_id=with_id))

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
        text = _ANGLE_URL_RE.sub(
            lambda m: stash(_angle_autolink_html(m.group(1))),
            text,
        )
        text = _FOOTNOTE_REF_RE.sub(replace_footnote, text)
        text = _escape_html_text(text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
        text = _STRIKETHROUGH_RE.sub(r"<del>\1</del>", text)
        for index, snippet in enumerate(placeholders):
            text = text.replace(f"\x00PH{index}\x00", snippet)
        return text

    def paragraph_interrupt(index: int) -> bool:
        line = lines[index]
        if not line.strip():
            return True
        if (
            _HEADING_RE.match(line)
            or _FENCE_RE.match(line)
            or _HR_RE.match(line)
            or _is_alert_block_open(line)
            or _unquote_blockquote_line(line) is not None
            or _is_table_start(lines, index)
        ):
            return True
        if re.match(r"^(\s*)[-*+]\s+", line) or re.match(r"^(\s*)\d+\.\s+", line):
            return True
        return False

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

        alert_info = _blockquote_alert_info(line)
        if alert_info:
            close_list()
            html, i = _consume_blockquote_alert(lines, i, alert_info, render_inline)
            out.append(html)
            continue

        qiita_kind = _qiita_note_kind(line)
        if qiita_kind is not None:
            close_list()
            html, i = _consume_colon_alert(
                lines,
                i,
                flavor="qiita",
                kind=qiita_kind,
                render_inline=render_inline,
            )
            out.append(html)
            continue

        zenn_kind = _zenn_message_kind(line)
        if zenn_kind is not None:
            close_list()
            html, i = _consume_colon_alert(
                lines,
                i,
                flavor="zenn",
                kind=zenn_kind,
                render_inline=render_inline,
            )
            out.append(html)
            continue

        details_summary = _details_open_summary(line)
        if details_summary is not None:
            close_list()
            html, i = _consume_details(lines, i, details_summary, render_inline)
            out.append(html)
            continue

        if _unquote_blockquote_line(line) is not None:
            close_list()
            html, i = _consume_blockquote(lines, i, render_inline)
            out.append(html)
            continue

        if _is_table_start(lines, i):
            close_list()
            html, i = _consume_table(lines, i, render_inline)
            out.append(html)
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
            task = _parse_task_item(item) if ul else None
            if task is not None:
                checked, rest = task
                box = _task_checkbox_html(checked)
                body = render_inline(rest)
                if body:
                    out.append(f"<li>{box} {body}</li>")
                else:
                    out.append(f"<li>{box}</li>")
            else:
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
        while i < len(lines) and not paragraph_interrupt(i):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{render_inline(' '.join(s.strip() for s in para))}</p>")

    close_list()
    if footnote_order:
        out.append(_footnotes_section_html(footnote_order, footnote_defs, render_inline))
    return "\n".join(out) + ("\n" if out else "")

_ALERT_STYLESHEET = """\
.markdown-alert {
  padding: 0.75em 1em;
  margin: 1em 0;
  border-left: 0.25em solid #57606a;
  background: #f6f8fa;
}
.markdown-alert-title {
  font-weight: 700;
  margin: 0 0 0.35em;
}
.markdown-alert-note,
.markdown-alert-info,
.markdown-alert-message { border-left-color: #0969da; }
.markdown-alert-tip { border-left-color: #1a7f37; }
.markdown-alert-important { border-left-color: #8250df; }
.markdown-alert-warning,
.markdown-alert-warn { border-left-color: #9a6700; }
.markdown-alert-caution,
.markdown-alert-alert { border-left-color: #cf222e; }
.markdown-alert[data-alert-flavor="github"] { background: #f6f8fa; }
.markdown-alert[data-alert-flavor="obsidian"] { border-radius: 0.25em; }
.markdown-alert[data-alert-flavor="qiita"] { border-left-width: 0.35em; }
.markdown-alert[data-alert-flavor="zenn"] { background: #fff8f0; }
"""

_DEFAULT_STYLESHEET_REST = """\
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #d0d7de; padding: 0.4em 0.8em; }
th { background: #f6f8fa; font-weight: 600; }
blockquote {
  margin: 1em 0;
  padding: 0 1em;
  border-left: 0.25em solid #d0d7de;
  color: #656d76;
}
pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre {
  padding: 0.8em;
  overflow: auto;
  background: #f6f8fa;
  border-radius: 0.25em;
}
h1, h2, h3, h4, h5, h6 { margin-top: 1.25em; margin-bottom: 0.5em; }
del { text-decoration: line-through; }
li input[type="checkbox"] { margin: 0 0.35em 0 0; vertical-align: middle; }
details {
  margin: 1em 0;
  border: 1px solid #d0d7de;
  border-radius: 0.25em;
  padding: 0.5em 0.8em;
}
summary { cursor: pointer; font-weight: 600; }
.footnotes {
  margin-top: 2em;
  padding-top: 0.75em;
  border-top: 1px solid #d0d7de;
  font-size: 0.9em;
}
.footnotes ol { padding-left: 1.5em; }
sup.footnote-ref { font-size: 0.75em; line-height: 0; }
.footnote-backref { text-decoration: none; }
"""


def alert_stylesheet() -> str:
    """Return compact CSS for ``.markdown-alert`` HTML from ``markdown_to_html``.

    Covers the shared ``.markdown-alert`` / ``.markdown-alert-title`` hooks,
    kind colors (``note`` / ``tip`` / ``warning`` / Qiita ``warn`` / Zenn
    ``alert``, …), and ``data-alert-flavor`` distinctions. No external
    URLs or font files. Embed next to converted HTML::

        <style>{alert_stylesheet()}</style>
    """
    return _ALERT_STYLESHEET


def default_stylesheet() -> str:
    """Return compact CSS for ``markdown_to_html`` output, including alerts.

    Includes :func:`alert_stylesheet` plus defaults for tables, blockquotes,
    fenced/inline code, headings, strikethrough (``del``), task-list
    checkboxes, ``details``/``summary``, and ``.footnotes``. Stdlib string
    only; no external URLs.
    Typical embedding::

        html = "<style>" + default_stylesheet() + "</style>\\n" + markdown_to_html(src)
    """
    return _ALERT_STYLESHEET + _DEFAULT_STYLESHEET_REST


def is_probably_url(value: str) -> bool:
    """Return True if ``value`` looks like an http(s) URL."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
