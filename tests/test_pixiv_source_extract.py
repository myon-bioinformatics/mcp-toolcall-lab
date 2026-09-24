"""Fixture-driven regression tests for the #60 real-source extraction contract.

Covers docs/pixiv_source_fixture_contract.md's acceptance criteria: valid
extraction, malformed/unknown input, Japanese text, whitespace/line endings,
empty fields, unknown fields, duplicate/repeated section-like structures, and
that markdown.py reuse (not a parallel parser) actually happened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_toolcall_lab.markdown_lib import parse_sections
from mcp_toolcall_lab.pixiv_source_extract import (
    PixivSourceExtractError,
    _ENGLISH_LABEL_RE,
    _PARENT_LABEL_RE,
    _labeled_field,
    _split_reading,
    extract_pixiv_source,
    fetch_pixiv_source_extract,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "pixiv_dictionary"
JA_READING_FIXTURE = FIXTURE_DIR / "source_ja_reading_nested.html"
ASCII_FLAT_FIXTURE = FIXTURE_DIR / "source_ascii_flat_duplicate.html"
LABELS_FIXTURE = FIXTURE_DIR / "source_english_parent_labels.html"
PROVENANCE_FIXTURE = FIXTURE_DIR / "source_fixture_provenance.json"
ALL_FIXTURES = [JA_READING_FIXTURE, ASCII_FLAT_FIXTURE, LABELS_FIXTURE]


# -- markdown.py reuse, not a parallel parser -------------------------------


def test_extraction_reuses_markdown_lib_helpers_not_a_parallel_parser() -> None:
    source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_source_extract.py").read_text(encoding="utf-8")
    assert "from .markdown_lib import load_markdown, own_body, parse_sections" in source
    assert "html_to_markdown" in source
    assert "class HTMLParser" not in source
    assert "import html.parser" not in source


def test_not_yet_wired_into_dictionary_tool_fetch_functions() -> None:
    source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_dictionary_tool.py").read_text(encoding="utf-8")
    assert "pixiv_source_extract" not in source


# -- Fixture: Japanese title+reading, H1 -> H3 heading-level jump ----------


def test_ja_reading_fixture_splits_title_and_reading() -> None:
    html = JA_READING_FIXTURE.read_text(encoding="utf-8")
    extract = extract_pixiv_source(html)
    assert extract.title == "サンプル項目"
    assert extract.reading == "さんぷるこうもく"
    assert extract.overview == "これは検証用の合成本文であり、実記事からの抜粋ではない。"
    assert extract.headings == [
        {"heading": "サンプル項目（さんぷるこうもく）", "level": 1},
        {"heading": "初出", "level": 3},
        {"heading": "概要", "level": 2},
    ]
    assert extract.parent_article == ""
    assert extract.english_title == ""


# -- Fixture: ASCII title, no reading, duplicate sibling headings ----------


def test_ascii_flat_fixture_has_no_reading_and_keeps_duplicate_headings() -> None:
    html = ASCII_FLAT_FIXTURE.read_text(encoding="utf-8")
    extract = extract_pixiv_source(html)
    assert extract.title == "SAMPLE TITLE"
    assert extract.reading == ""
    assert extract.overview == "Synthetic body for a title with no parenthesized reading."
    assert extract.headings == [
        {"heading": "SAMPLE TITLE", "level": 1},
        {"heading": "Notes", "level": 2},
        {"heading": "Notes", "level": 2},
    ]
    assert extract.parent_article == ""
    assert extract.english_title == ""


# -- Fixture: labeled English/parent-article lines -------------------------


def test_labels_fixture_extracts_english_title_and_parent_article() -> None:
    html = LABELS_FIXTURE.read_text(encoding="utf-8")
    extract = extract_pixiv_source(html)
    assert extract.title == "SAMPLE ALT TITLE"
    assert extract.english_title == "SAMPLE ALT TITLE (EN)"
    assert extract.parent_article == "SAMPLE PARENT WORK"
    assert "English: SAMPLE ALT TITLE (EN)" in extract.overview
    assert "Parent article: SAMPLE PARENT WORK" in extract.overview
    assert extract.headings == [
        {"heading": "SAMPLE ALT TITLE", "level": 1},
        {"heading": "Overview", "level": 2},
    ]


# -- Unknown fields are preserved in body, not silently dropped -----------


def test_unrecognized_labeled_line_is_preserved_in_body_not_dropped() -> None:
    html = "<h1>T</h1><p>Type: Something unrecognized.</p><h2>S</h2><p>Body text.</p>"
    extract = extract_pixiv_source(html)
    assert extract.parent_article == ""
    assert extract.english_title == ""
    assert "Type: Something unrecognized." in extract.body


# -- Malformed/missing heading input never fakes a title --------------------


def test_no_heading_falls_back_to_requested_title_and_empty_overview() -> None:
    html = "<p>Just prose, no headings at all.</p>"
    extract = extract_pixiv_source(html, requested_title="Requested Title")
    assert extract.title == "Requested Title"
    assert extract.reading == ""
    assert extract.overview == ""
    assert extract.headings == []
    assert "Just prose, no headings at all." in extract.body


def test_no_heading_and_no_requested_title_yields_empty_title() -> None:
    extract = extract_pixiv_source("<p>No headings here.</p>")
    assert extract.title == ""


def test_missing_markdown_py_raises_extract_error(monkeypatch) -> None:
    import mcp_toolcall_lab.pixiv_source_extract as pse

    monkeypatch.setattr(pse, "load_markdown", lambda: None)
    with pytest.raises(PixivSourceExtractError):
        extract_pixiv_source("<h1>Title</h1><p>Body.</p>")


def test_blank_markdown_conversion_raises_extract_error(monkeypatch) -> None:
    import mcp_toolcall_lab.pixiv_source_extract as pse

    class BlankMarkdown:
        def html_to_markdown(self, raw_html):
            return "   "

    monkeypatch.setattr(pse, "load_markdown", lambda: BlankMarkdown())
    with pytest.raises(PixivSourceExtractError):
        extract_pixiv_source("<h1>Title</h1><p>Body.</p>")


# -- Round-trip / stability --------------------------------------------------


@pytest.mark.parametrize("fixture_path", ALL_FIXTURES)
def test_extraction_is_deterministic_across_repeated_calls(fixture_path: Path) -> None:
    html = fixture_path.read_text(encoding="utf-8")
    first = extract_pixiv_source(html).as_dict()
    second = extract_pixiv_source(html).as_dict()
    assert first == second


@pytest.mark.parametrize("fixture_path", ALL_FIXTURES)
def test_round_trip_reparsing_body_preserves_headings(fixture_path: Path) -> None:
    html = fixture_path.read_text(encoding="utf-8")
    extract = extract_pixiv_source(html)
    reparsed = [{"heading": s.title, "level": s.level} for s in parse_sections(extract.body)]
    assert reparsed == extract.headings


def test_fetch_pixiv_source_extract_returns_plain_dict_with_expected_keys() -> None:
    html = JA_READING_FIXTURE.read_text(encoding="utf-8")
    result = fetch_pixiv_source_extract(html)
    assert isinstance(result, dict)
    assert set(result) == {
        "title",
        "reading",
        "overview",
        "parent_article",
        "english_title",
        "headings",
        "body",
    }


# -- _split_reading / _labeled_field: direct unit coverage ------------------
# Exercised directly (not only via HTML fixtures) so whitespace/line-ending
# handling is verified against Python's own well-defined str.splitlines()
# behavior rather than depending on how vendor/markdown.py's HTML parser
# happens to collapse inter-tag whitespace.


def test_split_reading_handles_ascii_and_fullwidth_parens() -> None:
    assert _split_reading("Foo (bar)") == ("Foo", "bar")
    assert _split_reading("サンプル項目（さんぷるこうもく）") == ("サンプル項目", "さんぷるこうもく")


def test_split_reading_without_parens_keeps_whole_line_as_title() -> None:
    assert _split_reading("SAMPLE TITLE") == ("SAMPLE TITLE", "")


def test_split_reading_empty_parens_falls_back_to_whole_line() -> None:
    assert _split_reading("Foo ()") == ("Foo ()", "")


def test_labeled_field_matches_english_and_japanese_labels() -> None:
    assert _labeled_field("English: Foo\nOther text", _ENGLISH_LABEL_RE) == "Foo"
    assert _labeled_field("英語表記：Foo", _ENGLISH_LABEL_RE) == "Foo"
    assert _labeled_field("Parent article: Bar", _PARENT_LABEL_RE) == "Bar"
    assert _labeled_field("親記事：Bar", _PARENT_LABEL_RE) == "Bar"


def test_labeled_field_returns_empty_when_label_absent() -> None:
    assert _labeled_field("no label here", _ENGLISH_LABEL_RE) == ""
    assert _labeled_field("", _PARENT_LABEL_RE) == ""


def test_labeled_field_tolerates_crlf_and_lone_cr_line_endings() -> None:
    assert _labeled_field("Intro\r\nEnglish: Foo\r\nMore", _ENGLISH_LABEL_RE) == "Foo"
    assert _labeled_field("Intro\rParent article: Bar\rMore", _PARENT_LABEL_RE) == "Bar"


# -- Fixture provenance is documented and stays in sync ---------------------


def test_source_fixture_provenance_covers_every_html_fixture() -> None:
    manifest = json.loads(PROVENANCE_FIXTURE.read_text(encoding="utf-8"))
    assert "_provenance" in manifest
    documented = {entry["file"] for entry in manifest["fixtures"]}
    on_disk = {p.name for p in ALL_FIXTURES}
    assert documented == on_disk
    for entry in manifest["fixtures"]:
        assert entry["synthetic"] is True
        assert (FIXTURE_DIR / entry["file"]).is_file()
