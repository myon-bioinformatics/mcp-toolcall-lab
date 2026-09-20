"""Load the vendored stdlib ``markdown.py`` without making it a package import.

The sibling repo ships one file. We keep a snapshot under ``vendor/`` so
Docker / GitHub Pages builds stay offline after checkout. If the file is
missing, callers fall back to the stub's own ATX splitter.

Vendored ``markdown.py`` owns Markdown ↔ HTML/CSS and the thin Markdown ↔
Kramdown IAL subset. This module does **not** reimplement
``markdown_to_html``, ``html_to_markdown``, ``default_stylesheet``,
``markdown_to_kramdown``, or ``ial`` — callers use ``load_markdown()``.

``Section``/``slugify``/``parse_sections``/``lookup_heading`` live here
too: the heading -> body split is "use the vendored module, else a local
ATX regex fallback" regardless of *what* corpus is being split (a
fixtures/stub_front/*.md file for stub_front.py, or a live Wikipedia
extract converted to ATX for wikipedia_tool.py) -- one implementation,
not one per caller. Fuzzy heading lookup for the stub / Wikipedia tools
stays here; it is not the library's ``extract_section``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

def _repo_root() -> Path:
    """``src/mcp_toolcall_lab/markdown_lib.py`` -> repo root, two levels up.

    This module is also concatenated into the standalone
    ``openwebui_mcp_mock.py`` / ``librechat_mcp_mock.py`` (see
    ``export.py``'s ``INLINE_MODULES``), which run from a flat ``/app/``
    with no such ancestry -- ``.parents[2]`` would raise ``IndexError``
    there. Fall back to the file's own directory; ``markdown_py_path()``
    already has an explicit ``/app/vendor/markdown.py`` candidate for
    that case.
    """
    here = Path(__file__).resolve()
    parents = here.parents
    return parents[2] if len(parents) > 2 else here.parent


REPO_ROOT = _repo_root()
PROVENANCE_PATH = REPO_ROOT / "vendor" / "markdown.provenance.json"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    slug: str
    body: str
    line: int


def slugify(title: str) -> str:
    lowered = title.casefold().strip()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def parse_sections(markdown: str) -> list[Section]:
    """Heading → body. Prefer vendored ``markdown.py``; ATX regex is the fallback.

    A section's body runs until the next heading whose level is <= its own,
    so a shallower heading's body includes any deeper subsections nested
    under it (e.g. an H2 with no prose of its own, only H3 children) instead
    of coming back empty. This matches ``extract_section`` (this module and
    vendored ``markdown.py``) and the Pages ``#wiki`` JS's own
    ``parseWikiSections`` -- one heading contract across every surface that
    slices Markdown/MediaWiki sections in this repo.

    ``split_sections`` itself (vendored or not) still cuts at *every*
    heading regardless of level -- that flat chunk list is only reassembled
    into inclusive bodies here, so the provenance-pinned vendored file
    (see ``assert_markdown_provenance``) never needs editing.
    """
    md = load_markdown()
    chunks: list[tuple[int, str, list[str]]] = []
    if md is not None and hasattr(md, "split_sections"):
        for part in md.split_sections(markdown):
            level = int(part.get("level") or 0)
            title = str(part.get("title") or "")
            raw = str(part.get("content") or "")
            chunks.append((level, title, raw.splitlines(keepends=True)))
    else:
        lines = markdown.splitlines(keepends=True)
        found: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            match = _HEADING_RE.match(line.rstrip("\r\n"))
            if match:
                found.append((index, len(match.group(1)), match.group(2).strip()))
        for idx, (start, level, title) in enumerate(found):
            end = found[idx + 1][0] if idx + 1 < len(found) else len(lines)
            chunks.append((level, title, lines[start:end]))

    sections: list[Section] = []
    line = 1
    for i, (level, title, chunk_lines) in enumerate(chunks):
        raw = "".join(chunk_lines)
        if level > 0 and title:
            nested: list[str] = []
            j = i + 1
            while j < len(chunks) and chunks[j][0] > level:
                nested.extend(chunks[j][2])
                j += 1
            body = "".join(chunk_lines[1:] + nested).strip()
            sections.append(Section(level, title, slugify(title), body, line))
        line += raw.count("\n") or 1
    return sections


def lookup_heading(query: str, sections: list[Section], *, fuzzy: bool = True) -> Section | None:
    needle = query.strip()
    if needle.startswith("#"):
        needle = needle.lstrip("#").strip()
    if not needle:
        return None
    slug = slugify(needle)
    folded = needle.casefold()
    for section in sections:
        if section.title == needle or section.title.casefold() == folded or section.slug == slug:
            return section
    if not fuzzy:
        return None
    for section in sections:
        title = section.title.casefold()
        if title.startswith(folded) or folded.startswith(title) or folded in title:
            return section
    return None


def markdown_py_path() -> Path | None:
    override = os.environ.get("MARKDOWN_PY")
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.append(REPO_ROOT / "vendor" / "markdown.py")
    candidates.append(Path("/app/vendor/markdown.py"))
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_markdown() -> ModuleType | None:
    path = markdown_py_path()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("lab_vendored_markdown", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_provenance() -> dict[str, str]:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def assert_markdown_provenance(path: Path | None = None) -> dict[str, str]:
    """Fail if the vendored file drifted from the recorded commit/blob."""
    recorded = load_provenance()
    target = path or markdown_py_path()
    if target is None:
        raise FileNotFoundError("vendor/markdown.py is missing")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    blob = git_blob_sha(target)
    if blob != recorded["blob_sha"] or digest != recorded["sha256"]:
        raise ValueError(
            f"vendored markdown.py does not match {PROVENANCE_PATH.name}: "
            f"blob={blob} sha256={digest} recorded={recorded}"
        )
    return recorded
