"""Fixture-driven regression tests for the #61 Pixiv markup normalization layer.

Covers docs/pixiv_markup_normalization.md's SUPPORTED / UNSUPPORTED contract:
observed heading markers, Pixiv wiki links, pixivimage tokens (with/without a
size suffix), the unsupported table-header marker, unspaced list-link lines,
NEXT navigation links, native emphasis, Unicode/whitespace, duplicates, and
malformed forms -- plus normalization-stability (idempotence) coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_toolcall_lab.pixiv_markup_normalize import (
    PixivMarkupNormalizeError,
    normalize_pixiv_markup,
)

ROOT = Path(__file__).resolve().parents[1]
CASES_FIXTURE = ROOT / "fixtures" / "pixiv_dictionary" / "markup_normalization_cases.json"
CASES = json.loads(CASES_FIXTURE.read_text(encoding="utf-8"))["cases"]


# -- markdown.py reuse, not a parallel renderer -----------------------------


def test_normalization_reuses_markdown_lib_make_link_and_make_image() -> None:
    source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_markup_normalize.py").read_text(encoding="utf-8")
    assert "from .markdown_lib import load_markdown" in source
    assert "md.make_link" in source
    assert "md.make_image" in source
    assert "class HTMLParser" not in source
    assert "import html.parser" not in source


def test_not_yet_wired_into_source_extract_or_dictionary_tool() -> None:
    extract_source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_source_extract.py").read_text(encoding="utf-8")
    tool_source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_dictionary_tool.py").read_text(encoding="utf-8")
    assert "pixiv_markup_normalize" not in extract_source
    assert "pixiv_markup_normalize" not in tool_source


# -- Fixture provenance is documented and stays in sync ---------------------


def test_markup_cases_fixture_has_provenance_and_unique_names() -> None:
    manifest = json.loads(CASES_FIXTURE.read_text(encoding="utf-8"))
    assert "_provenance" in manifest
    names = [case["name"] for case in manifest["cases"]]
    assert len(names) == len(set(names))
    assert len(names) >= 15


# -- Regression: every fixture case normalizes to its documented output ----


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_fixture_case_matches_expected_normalization(case: dict) -> None:
    assert normalize_pixiv_markup(case["input"]) == case["expected"]


# -- Idempotence / stability: normalizing twice equals normalizing once ----


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_fixture_case_normalization_is_idempotent(case: dict) -> None:
    once = normalize_pixiv_markup(case["input"])
    twice = normalize_pixiv_markup(once)
    assert twice == once


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_fixture_case_normalization_is_deterministic_across_repeated_calls(case: dict) -> None:
    first = normalize_pixiv_markup(case["input"])
    second = normalize_pixiv_markup(case["input"])
    assert first == second


# -- Category-specific spot checks (documented SUPPORTED behavior) ---------


def test_piped_link_anchor_parens_are_percent_encoded_not_left_literal() -> None:
    out = normalize_pixiv_markup("[[死神>死神(BLEACH)]]")
    assert "(" not in out.split("](", 1)[1]
    assert "%28" in out and "%29" in out


def test_pixivimage_without_size_has_no_title_quotes() -> None:
    out = normalize_pixiv_markup("[pixivimage:61456650]")
    assert out == "![pixiv image 61456650](pixivimage:61456650)"
    assert '"' not in out


def test_pixivimage_with_size_keeps_size_as_title() -> None:
    out = normalize_pixiv_markup("[pixivimage:61456650:ms]")
    assert out == '![pixiv image 61456650](pixivimage:61456650 "ms")'


def test_unspaced_list_dash_only_triggers_directly_before_double_bracket() -> None:
    # A bare "-word" line (no [[ immediately after the dash) is not a Pixiv
    # list-link token; it must be left untouched rather than gaining a space.
    assert normalize_pixiv_markup("-word") == "-word"


def test_next_arrow_only_triggers_directly_before_double_bracket() -> None:
    # "NEXT▶︎" with trailing prose (no [[ link) is left untouched.
    assert normalize_pixiv_markup("NEXT▶︎ see also") == "NEXT▶︎ see also"


def test_blank_line_runs_are_never_collapsed() -> None:
    text = "a\n\n\n\nb"
    assert normalize_pixiv_markup(text) == text


def test_lf_only_input_with_trailing_blank_lines_is_untouched() -> None:
    # No \r present, so CRLF/CR normalization is a no-op; trailing blank
    # lines are neither stripped nor collapsed.
    text = "a\nb\n\n"
    assert normalize_pixiv_markup(text) == text


# -- Error path: vendor markdown.py's link/image builders are required -----


def test_missing_markdown_py_raises_normalize_error(monkeypatch) -> None:
    import mcp_toolcall_lab.pixiv_markup_normalize as pmn

    monkeypatch.setattr(pmn, "load_markdown", lambda: None)
    with pytest.raises(PixivMarkupNormalizeError):
        normalize_pixiv_markup("[[久保帯人]]")


def test_markdown_py_missing_make_link_raises_normalize_error(monkeypatch) -> None:
    import mcp_toolcall_lab.pixiv_markup_normalize as pmn

    class NoLinkMarkdown:
        def make_image(self, alt, url, title=None):
            return f"![{alt}]({url})"

    monkeypatch.setattr(pmn, "load_markdown", lambda: NoLinkMarkdown())
    with pytest.raises(PixivMarkupNormalizeError):
        normalize_pixiv_markup("[[久保帯人]]")
