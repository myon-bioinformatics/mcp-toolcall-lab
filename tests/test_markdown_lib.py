"""markdown_lib.parse_sections -- inclusive nested section bodies.

BLEACH-shaped corpus: an H2 parent (``あらすじ``) followed by two H3
children, then a sibling H2. The parent's body must include both
children's headings/text and stop only at the next H2 -- otherwise the
Pages ``#wiki`` UI's "labels say inclusive but Docker/MCP say exclusive"
parity gap (see issue #34) comes back for the Python side. Both the
vendored ``markdown.py`` path and the local ATX-regex fallback must agree.
"""

from __future__ import annotations

from mcp_toolcall_lab.markdown_lib import load_markdown, parse_sections

BLEACH_MARKDOWN = (
    "# BLEACH\n"
    "\n"
    "## あらすじ\n"
    "\n"
    "### 死神代行篇\n"
    "arc1 body\n"
    "\n"
    "### 尸魂界篇\n"
    "arc2 body\n"
    "\n"
    "## 登場人物\n"
    "people body\n"
)


def _assert_bleach_shape(sections) -> None:
    by_title = {section.title: section for section in sections}
    assert by_title.keys() >= {"あらすじ", "死神代行篇", "尸魂界篇", "登場人物"}

    lead = by_title["あらすじ"]
    assert lead.level == 2
    assert "### 死神代行篇" in lead.body
    assert "arc1 body" in lead.body
    assert "### 尸魂界篇" in lead.body
    assert "arc2 body" in lead.body
    assert "登場人物" not in lead.body
    assert "people body" not in lead.body

    arc1 = by_title["死神代行篇"]
    assert arc1.level == 3
    assert arc1.body == "arc1 body"

    people = by_title["登場人物"]
    assert people.level == 2
    assert people.body == "people body"


def test_parse_sections_inclusive_nested_bleach_shape_vendored() -> None:
    assert load_markdown() is not None, "vendor/markdown.py must be present for this check"
    _assert_bleach_shape(parse_sections(BLEACH_MARKDOWN))


def test_parse_sections_inclusive_nested_bleach_shape_fallback(monkeypatch) -> None:
    import mcp_toolcall_lab.markdown_lib as markdown_lib

    monkeypatch.setattr(markdown_lib, "load_markdown", lambda: None)
    _assert_bleach_shape(parse_sections(BLEACH_MARKDOWN))


def test_parse_sections_sibling_h2_still_stops_at_next_h2() -> None:
    text = "## A\nbody a\n\n## B\nbody b\n"
    sections = parse_sections(text)
    assert [s.title for s in sections] == ["A", "B"]
    assert sections[0].body == "body a"
    assert sections[1].body == "body b"
