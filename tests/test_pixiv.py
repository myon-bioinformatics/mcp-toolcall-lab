"""Pixiv and niconico: dictionary tool, markup normalization, source extraction, live evidence and the Pages client."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab import pixiv_dictionary_tool as pt
from mcp_toolcall_lab.adapters.niconico_adapter import (
    VERSION_URL, build_search_url, classify_page, completion_state, content_url, fetch_json, fetch_version, next_delay_seconds, normalize_item,
    paged_search, snapshot_consistent,
)
from mcp_toolcall_lab.markdown_lib import parse_sections
from mcp_toolcall_lab.pixiv_dictionary_tool import PixivDictionaryFetchError
from mcp_toolcall_lab.pixiv_markup_normalize import PixivMarkupNormalizeError, normalize_pixiv_markup, pixiv_sections, pixiv_to_markdown
from mcp_toolcall_lab.pixiv_source_extract import (
    PixivSourceExtractError, _ENGLISH_LABEL_RE, _PARENT_LABEL_RE, _labeled_field, _split_reading, extract_pixiv_source, fetch_pixiv_source_extract,
)
from mcp_toolcall_lab.stub_front import PAGES_PIXIV_JS_SOURCE, _pixiv_error_from_body, pixiv_page_response


# ═══ · pixiv dictionary tool ═════════════════════════════════════════════════════════════════════════════

FIXTURE = ROOT / "fixtures" / "pixiv_dictionary" / "sample_article.html"


def test_pixiv_html_uses_markdown_py_and_exposes_hierarchical_toc(monkeypatch):
    monkeypatch.setenv(pt.FIXTURE_ENV, str(FIXTURE))
    pt.reset_pixiv_dictionary_cache()
    article = pt.fetch_pixiv_dictionary_article("テスト記事")
    assert article["canonical_title"] == "テスト記事"
    assert "## 概要" in article["markdown"]
    assert "### 来歴" in article["markdown"]
    assert {"heading": "概要", "level": 2} in article["headings"]
    assert {"heading": "来歴", "level": 3} in article["headings"]


def test_japanese_heading_does_not_alias_empty_ascii_slug(monkeypatch):
    monkeypatch.setenv(pt.FIXTURE_ENV, str(FIXTURE))
    pt.reset_pixiv_dictionary_cache()
    result = pt.fetch_pixiv_dictionary_section("テスト記事", "来歴")
    assert result[0]["heading"] == "来歴"
    assert result[0]["body"] == "1999年に活動を開始した。"


def test_heading_switch_reuses_cached_html(monkeypatch):
    monkeypatch.setenv(pt.FIXTURE_ENV, str(FIXTURE))
    pt.reset_pixiv_dictionary_cache()
    first = pt.fetch_pixiv_dictionary_article("テスト記事")
    second = pt.fetch_pixiv_dictionary_section("テスト記事", "来歴")
    assert first["cache"] == "miss"
    assert second[0]["body"] == "1999年に活動を開始した。"
    assert pt.fetch_pixiv_dictionary_article("テスト記事")["cache"] == "hit"


def test_pixiv_fetch_uses_one_encoded_article_url(monkeypatch):
    monkeypatch.delenv(pt.FIXTURE_ENV, raising=False)
    pt.reset_pixiv_dictionary_cache()
    seen = []

    class Headers:
        def get_content_charset(self):
            return "utf-8"

    class Response:
        headers = Headers()
        def read(self):
            return FIXTURE.read_bytes()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return Response()

    monkeypatch.setattr(pt.urllib.request, "urlopen", fake_urlopen)
    pt.fetch_pixiv_dictionary_article("ユーグラム・ハッシュヴァルト")
    pt.fetch_pixiv_dictionary_section("ユーグラム・ハッシュヴァルト", "概要")
    assert len(seen) == 1
    assert seen[0].startswith("https://dic.pixiv.net/a/")
    assert "%E3%83%A6" in seen[0]


def test_http_error_from_upstream_carries_stage_and_status(monkeypatch):
    monkeypatch.delenv(pt.FIXTURE_ENV, raising=False)
    pt.reset_pixiv_dictionary_cache()

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(pt.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(pt.PixivDictionaryFetchError) as exc_info:
        pt.fetch_pixiv_dictionary_article("NARUTO")
    assert exc_info.value.stage == "upstream_http"
    assert exc_info.value.upstream_status == 403


def test_network_error_from_upstream_is_upstream_network_stage(monkeypatch):
    monkeypatch.delenv(pt.FIXTURE_ENV, raising=False)
    pt.reset_pixiv_dictionary_cache()

    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(pt.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(pt.PixivDictionaryFetchError) as exc_info:
        pt.fetch_pixiv_dictionary_article("NARUTO")
    assert exc_info.value.stage == "upstream_network"
    assert exc_info.value.upstream_status is None


def test_empty_title_raises_input_stage_error():
    with pytest.raises(pt.PixivDictionaryFetchError) as exc_info:
        pt.fetch_pixiv_dictionary_article("   ")
    assert exc_info.value.stage == "input"
    assert exc_info.value.upstream_status is None


def test_empty_markdown_conversion_is_convert_stage(monkeypatch):
    monkeypatch.setenv(pt.FIXTURE_ENV, str(FIXTURE))
    pt.reset_pixiv_dictionary_cache()

    class BlankMarkdown:
        def html_to_markdown(self, raw_html):
            return "   "

    monkeypatch.setattr(pt, "load_markdown", lambda: BlankMarkdown())
    with pytest.raises(pt.PixivDictionaryFetchError) as exc_info:
        pt.fetch_pixiv_dictionary_article("テスト記事")
    assert exc_info.value.stage == "convert"


def test_article_pipeline_applies_pixiv_normalization_before_sections(monkeypatch):
    monkeypatch.setenv(pt.FIXTURE_ENV, str(FIXTURE))
    pt.reset_pixiv_dictionary_cache()
    seen = []

    def fake_pixiv_to_markdown(markdown):
        seen.append(markdown)
        return markdown + "\n\n# 【TAG】\nnormalized body"

    monkeypatch.setattr(pt, "pixiv_to_markdown", fake_pixiv_to_markdown)
    article = pt.fetch_pixiv_dictionary_article("テスト記事")
    assert len(seen) == 1
    assert {"heading": "【TAG】", "level": 1} in article["headings"]
    result = pt.fetch_pixiv_dictionary_section("テスト記事", "【TAG】")
    assert result == [{"heading": "【TAG】", "level": "1", "body": "normalized body"}]
    assert len(seen) == 1


def test_article_pipeline_normalizes_observed_pixiv_tokens(monkeypatch):
    monkeypatch.setenv(pt.FIXTURE_ENV, str(FIXTURE))
    pt.reset_pixiv_dictionary_cache()

    def fake_html_to_markdown(raw_html):
        return "*【INFORMATION】／作品情報\n本文\n-[[黒崎一護]]"

    monkeypatch.setattr(pt, "_html_to_markdown", fake_html_to_markdown)
    article = pt.fetch_pixiv_dictionary_article("テスト記事")
    assert article["markdown"].startswith("# 【INFORMATION】／作品情報")
    assert "- [黒崎一護](黒崎一護)" in article["markdown"]
    section = pt.fetch_pixiv_dictionary_section("テスト記事", "【INFORMATION】／作品情報")
    assert section[0]["body"] == "本文\n- [黒崎一護](黒崎一護)"


# ═══ · pixiv live evidence ═══════════════════════════════════════════════════════════════════════════════
# Contracts for live Pixiv evidence in the stub-pages lane.

def test_pixiv_adapter_uses_public_article_url_without_fixture_by_default() -> None:
    source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_dictionary_tool.py").read_text(encoding="utf-8")
    assert 'PIXIV_ARTICLE = "https://dic.pixiv.net/a/{title}"' in source
    assert "urllib.request.urlopen(request, timeout=TIMEOUT)" in source
    assert "MCP_TOOLCALL_LAB_PIXIV_FIXTURE" in source


def test_stub_pages_captures_real_pixiv_result_in_chromium_and_webkit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert 'PIXIV_URL="http://127.0.0.1:8765/pixiv?title=NARUTO"' in workflow
    assert "https://dic.pixiv.net/a/NARUTO" in workflow
    assert 'grep -q \'data-testid="pixiv-result"\'' in workflow
    assert '--browser=chromium --device="Desktop Chrome"' in workflow
    assert '--browser=webkit --device="iPhone 13"' in workflow
    assert "pixiv-live-desktop.png" in workflow
    assert "pixiv-live-mobile.png" in workflow
    assert "test -s test-results/pixiv-pages-mobile.png" in workflow
    assert 'od -An -tx1 -N8 test-results/pixiv-pages-mobile.png' in workflow
    assert "test -s test-results/pixiv-live-mobile.png" in workflow
    assert "static WebKit exit status: $WEBKIT_STATUS" in workflow
    assert "live WebKit exit status: $LIVE_WEBKIT_STATUS" in workflow
    assert "pixiv-pages-webkit.log" in workflow
    assert "pixiv-live-webkit.log" in workflow
    assert "HTTP status: $HTTP_STATUS" in workflow
    assert "pixiv-live.html (first 120 lines)" in workflow
    assert "logs --tail=200 stub-front" in workflow
    assert "logs --tail=120 stub-front" not in workflow
    assert "missing pixiv-result marker" in workflow
    assert "missing expected Pixiv source URL" in workflow
    assert workflow.count('"src/mcp_toolcall_lab/pixiv_dictionary_tool.py"') == 2


def test_live_pixiv_fetch_is_split_and_strictly_classified() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stub-pages.yml").read_text(encoding="utf-8")
    assert "name: Live Pixiv fetch (stub-front -> dic.pixiv.net)" in workflow
    assert "id: pixiv_live" in workflow
    assert "name: Capture live Pixiv screenshots" in workflow
    assert "if: steps.pixiv_live.outputs.live == 'ok'" in workflow
    assert "--max-time 30" in workflow
    assert "--dump-header test-results/pixiv-live.headers" in workflow
    assert "X-Pixiv-Stage" in workflow
    assert "X-Pixiv-Upstream-Status" in workflow
    assert "classification: $CLASSIFICATION" in workflow
    assert "::warning title=Pixiv live skipped: $CLASSIFICATION::$DETAIL" in workflow
    assert "::error title=Pixiv live: $1::$2" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    for category in (
        "stub-unreachable",
        "upstream-rate-limited-",
        "upstream-http-",
        "upstream-network",
        "stub-internal-",
        "bad-request",
        "unexpected-http-",
        "result-marker-missing",
        "source-url-missing",
    ):
        assert category in workflow
    # Only the two explicitly accepted upstream conditions may produce a skip.
    skip_condition = (
        '[ "$CLASSIFICATION" = "upstream-http-403" ] || '
        '[ "$CLASSIFICATION" = "upstream-rate-limited-429" ]'
    )
    assert skip_condition in workflow
    assert workflow.count('echo "live=skipped" >> "$GITHUB_OUTPUT"') == 1
    assert 'echo "live=ok" >> "$GITHUB_OUTPUT"' in workflow


def test_pixiv_error_headers_are_ascii_safe_for_japanese_title(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError(
            f"article {arguments['title']!r} returned HTTP 403",
            stage="upstream_http",
            upstream_status=403,
        )

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    status, body, headers = pixiv_page_response(title="ナルト")
    assert status == 502
    assert 'data-testid="pixiv-error"' in body
    assert 'data-testid="pixiv-result"' not in body
    assert "X-Pixiv-Error" not in headers
    for value in headers.values():
        value.encode("latin-1")


def test_pixiv_success_without_cache_reports_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_toolcall_lab.stub_front.dispatch_tool",
        lambda name, arguments: {"canonical_title": arguments["title"], "markdown": "ok"},
    )
    status, body, headers = pixiv_page_response(title="NARUTO")
    assert status == 200
    assert headers["X-Pixiv-Cache"] == "unknown"
    assert 'data-testid="pixiv-result"' in body


def test_pixiv_error_is_kept_in_body_not_wire_headers(monkeypatch) -> None:
    def boom(name, arguments):
        raise PixivDictionaryFetchError(
            f"article {arguments['title']!r} returned HTTP 403",
            stage="upstream_http",
            upstream_status=403,
        )

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    _status, body, headers = pixiv_page_response(title="テスト記事")
    assert "X-Pixiv-Error" not in headers
    assert "テスト記事" in body


def test_pixiv_error_log_extraction_preserves_unescaped_japanese_text(monkeypatch) -> None:
    expected = "article 'ナルト & テスト' returned HTTP 403"

    def boom(name, arguments):
        raise PixivDictionaryFetchError(
            expected,
            stage="upstream_http",
            upstream_status=403,
        )

    monkeypatch.setattr("mcp_toolcall_lab.stub_front.dispatch_tool", boom)
    _status, body, _headers = pixiv_page_response(title="ナルト & テスト")
    assert _pixiv_error_from_body(body) == expected
    assert _pixiv_error_from_body("<p>no pixiv error marker</p>") is None


# ═══ · pixiv markup normalize ════════════════════════════════════════════════════════════════════════════
# Fixture-driven regression tests for the #61 Pixiv markup normalization layer.
#
# Covers docs/pixiv-markup-normalization.md's SUPPORTED / UNSUPPORTED contract:
# observed heading markers, Pixiv wiki links, pixivimage tokens (with/without a
# size suffix), the unsupported table-header marker, unspaced list-link lines,
# NEXT navigation links, native emphasis, Unicode/whitespace, duplicates, and
# malformed forms -- plus normalization-stability (idempotence) coverage.

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


def test_normalizer_is_wired_into_dictionary_tool_but_not_source_extract() -> None:
    extract_source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_source_extract.py").read_text(encoding="utf-8")
    tool_source = (ROOT / "src" / "mcp_toolcall_lab" / "pixiv_dictionary_tool.py").read_text(encoding="utf-8")
    assert "pixiv_markup_normalize" not in extract_source
    assert "pixiv_markup_normalize" in tool_source


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
    assert pixiv_to_markdown(case["input"]) == case["expected"]


# -- Idempotence / stability: normalizing twice equals normalizing once ----


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_fixture_case_normalization_is_idempotent(case: dict) -> None:
    once = pixiv_to_markdown(case["input"])
    twice = pixiv_to_markdown(once)
    assert twice == once


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_fixture_case_normalization_is_deterministic_across_repeated_calls(case: dict) -> None:
    first = pixiv_to_markdown(case["input"])
    second = pixiv_to_markdown(case["input"])
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


def test_pixiv_heading_adapter_feeds_shared_section_parser() -> None:
    source = "*【INFORMATION】／作品情報\n本文\n**【DETAIL】／詳細\n子本文"
    converted = pixiv_to_markdown(source)
    assert converted.startswith("# 【INFORMATION】／作品情報")
    sections = pixiv_sections(source)
    assert [(s.level, s.title) for s in sections] == [
        (1, "【INFORMATION】／作品情報"),
        (2, "【DETAIL】／詳細"),
    ]
    assert "子本文" in sections[0].body


def test_pixiv_heading_adapter_does_not_reinterpret_emphasis() -> None:
    source = "***我等は***　***姿無きが故に***\n*ordinary emphasis*"
    assert pixiv_to_markdown(source) == source


def test_pixiv_heading_adapter_preserves_unrecognized_star_line() -> None:
    source = "*not a pixiv labelled heading"
    assert pixiv_to_markdown(source) == source


def test_pixiv_to_markdown_is_idempotent_with_headings() -> None:
    source = "*【TAG】／関連タグ\n-[[BLEACH]]"
    once = pixiv_to_markdown(source)
    assert pixiv_to_markdown(once) == once


def test_token_normalizer_does_not_promote_pixiv_heading() -> None:
    source = "*【INFORMATION】／作品情報"
    assert normalize_pixiv_markup(source) == source
    assert pixiv_to_markdown(source) == "# 【INFORMATION】／作品情報"


def test_pixiv_heading_adapter_reuses_vendor_heading_builder(monkeypatch) -> None:
    import mcp_toolcall_lab.pixiv_markup_normalize as pmn
    real = pmn.load_markdown()
    calls = []

    class Proxy:
        make_link = staticmethod(real.make_link)
        make_image = staticmethod(real.make_image)

        @staticmethod
        def heading(text, level=1):
            calls.append((text, level))
            return real.heading(text, level)

    monkeypatch.setattr(pmn, "load_markdown", lambda: Proxy())
    assert pmn.pixiv_to_markdown("**【OVERVIEW】／概要") == "## 【OVERVIEW】／概要"
    assert calls == [("【OVERVIEW】／概要", 2)]


def test_bare_pixiv_label_heading_is_supported_without_invented_title() -> None:
    source = "*【TAG】"
    assert normalize_pixiv_markup(source) == source
    assert pixiv_to_markdown(source) == "# 【TAG】"


def test_pixiv_to_markdown_separates_heading_and_body_with_blank_lines() -> None:
    source = "*【A】／a\n本文\n**【B】／b\n本文2"
    assert pixiv_to_markdown(source) == "# 【A】／a\n\n本文\n\n## 【B】／b\n\n本文2"


def test_pixiv_to_markdown_blank_lines_are_not_duplicated() -> None:
    source = "*【A】／a\n\n本文\n\n**【B】／b\n\n本文2"
    assert pixiv_to_markdown(source) == "# 【A】／a\n\n本文\n\n## 【B】／b\n\n本文2"


def test_pixiv_to_markdown_blank_lines_leave_unrecognized_star_lines_alone() -> None:
    source = "本文\n*not a pixiv labelled heading\n続き"
    assert pixiv_to_markdown(source) == source


# ═══ · pixiv source extract ══════════════════════════════════════════════════════════════════════════════
# Fixture-driven regression tests for the #60 real-source extraction contract.
#
# Covers docs/pixiv-source-fixture-contract.md's acceptance criteria: valid
# extraction, malformed/unknown input, Japanese text, whitespace/line endings,
# empty fields, unknown fields, duplicate/repeated section-like structures, and
# that markdown.py reuse (not a parallel parser) actually happened.

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


# ═══ · pages pixiv ═══════════════════════════════════════════════════════════════════════════════════════
# Browser pixiv Encyclopedia client for published Pages #pixiv — no live network.
#
# This PR retires the old browser-side Search/fetch of ``dic.pixiv.net`` (which
# depended on a CORS allowance that site never grants) in favor of the
# Open -> Source -> Extract workflow: the panel generates and validates the
# real ``/a/{title}``, ``/history/{title}``, and
# ``/history/{title}/{revision_id}/source`` URLs so a visitor can open them,
# then normalizes source text pasted back from Pixiv's own 原文表示 (view
# source) page into a stable structured extract. Everything below runs
# against pasted text only; ``pages_pixiv.js`` makes no ``fetch()`` call.
#
# The URL contract table (valid/invalid revision-source URLs, ``buildSourceUrl``
# round trips) lives in ``fixtures/pixiv_dictionary/history_source_urls.json``
# as data rather than inline JS so the contract can be read/extended without
# touching the test script.

URL_FIXTURE = ROOT / "fixtures" / "pixiv_dictionary" / "history_source_urls.json"
NORMALIZATION_FIXTURE = ROOT / "fixtures" / "pixiv_dictionary" / "markup_normalization_cases.json"

_PAGES_PIXIV_CONTRACT = r"""
const fs = require("fs");
const pixiv = require(process.argv[1]);
const assert = require("assert");
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const normalizationFixtures = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

// -- URL generation --------------------------------------------------
assert.strictEqual(pixiv.buildArticleUrl("Bleach"), "https://dic.pixiv.net/a/Bleach");
assert.strictEqual(
  pixiv.buildArticleUrl("ブリーチ"),
  "https://dic.pixiv.net/a/" + encodeURIComponent("ブリーチ")
);
assert.strictEqual(pixiv.buildArticleUrl(""), "");
assert.strictEqual(pixiv.buildArticleUrl("  "), "");

assert.strictEqual(pixiv.buildHistoryUrl("BLEACH"), "https://dic.pixiv.net/history/BLEACH");
assert.strictEqual(pixiv.buildHistoryUrl(""), "");

// -- buildSourceUrl: fixture-driven valid/invalid/round-trip cases ----
// Percent-encoding (e.g. title "A/B" -> "A%2FB") is computed here via
// encodeURIComponent, not hand-computed in the fixture, so the fixture file
// never hardcodes a percent-encoding byte sequence that could silently drift
// from what encodeURIComponent actually produces.
fixtures.build_source_url.forEach(function (c) {
  var expected = c.expectedEncodeTitle
    ? "https://dic.pixiv.net/history/" + encodeURIComponent(c.title) + "/" + c.revisionId + "/source"
    : c.expected;
  assert.strictEqual(
    pixiv.buildSourceUrl(c.title, c.revisionId),
    expected,
    (c.reason || "buildSourceUrl") + ": " + JSON.stringify(c)
  );
});

// -- parseSourceUrl: fixture-driven valid cases (JP/raw-Unicode/percent- --
// encoded titles, title-with-slash -> %2F, trailing slash, query, fragment,
// and the 15-digit revision-id cap) ------------------------------------
fixtures.valid_source_urls.forEach(function (c) {
  var url = c.encodeTitleInUrl
    ? "https://dic.pixiv.net/history/" + encodeURIComponent(c.rawTitle) + "/" + c.revisionId + "/source"
    : c.url;
  var result = pixiv.parseSourceUrl(url);
  assert.strictEqual(result.ok, true, c.label + ": " + url);
  assert.strictEqual(result.title, c.title, c.label);
  assert.strictEqual(result.revisionId, c.revisionId, c.label);
});

// -- parseSourceUrl: fixture-driven invalid cases (wrong host, host-suffix -
// trick, http instead of https, zero/leading-zero/over-cap revision id, --
// non-numeric revision id, non-source URLs, empty/garbage input) --------
fixtures.invalid_source_urls.forEach(function (c) {
  var result = pixiv.parseSourceUrl(c.url);
  assert.strictEqual(result.ok, false, c.label + ": " + c.url);
  assert.strictEqual(result.error, pixiv.INVALID_SOURCE_URL, c.label);
});

// -- Extract: HTML-shaped pasted source --------------------------------
const htmlExtract = pixiv.extractSource({
  sourceText:
    "<h1>テスト記事（てすときじ）</h1><p>これは概要本文。</p>" +
    "<h2>来歴</h2><p>1999年に活動を開始した。</p>",
});
assert.strictEqual(htmlExtract.ok, true);
assert.strictEqual(htmlExtract.title, "テスト記事");
assert.strictEqual(htmlExtract.reading, "てすときじ");
assert.ok(htmlExtract.overview.includes("これは概要本文。"));
assert.deepStrictEqual(htmlExtract.headings, [
  { level: 1, heading: "テスト記事（てすときじ）" },
  { level: 2, heading: "来歴" },
]);
assert.ok(htmlExtract.body.includes("1999年に活動を開始した。"));

// -- Extract: plain pasted text (no HTML heading markers available) ---
const plainExtract = pixiv.extractSource({
  title: "BLEACH",
  sourceText: "BLEACH\nA shinigami story.\nSecond paragraph.",
});
assert.strictEqual(plainExtract.ok, true);
assert.strictEqual(plainExtract.title, "BLEACH");
assert.deepStrictEqual(plainExtract.headings, []);
assert.ok(plainExtract.body.includes("A shinigami story."));


// -- Shared fixture pins browser/Python normalization parity ---------------
normalizationFixtures.cases.forEach(function (c) {
  var actual = pixiv.pixivToMarkdown(c.input);
  assert.strictEqual(actual, c.expected, c.name);
  assert.strictEqual(pixiv.pixivToMarkdown(actual), actual, c.name + " idempotence");
});

// -- Pages mirrors the #61/#62 conservative Pixiv normalization ----------
const observedShape = [
  "*【INFORMATION】／作品情報",
  "-[[黒崎一護]]",
  "[pixivimage:61456650]",
  "NEXT▶︎[[獄頤鳴鳴篇]]",
  "[[死神>死神(BLEACH)]]",
].join("\n");
const normalizedShape = pixiv.pixivToMarkdown(observedShape);
assert.ok(normalizedShape.includes("# 【INFORMATION】／作品情報"));
assert.ok(normalizedShape.includes("- [黒崎一護](黒崎一護)"));
assert.ok(normalizedShape.includes("![pixiv image 61456650](pixivimage:61456650)"));
assert.ok(normalizedShape.includes("NEXT ▶︎ [獄頤鳴鳴篇](獄頤鳴鳴篇)"));
assert.ok(normalizedShape.includes("[死神](死神%28BLEACH%29)"));
assert.strictEqual(pixiv.pixivToMarkdown(normalizedShape), normalizedShape);

const normalizedExtract = pixiv.extractSource({
  title: "synthetic",
  sourceText: observedShape,
});
assert.strictEqual(normalizedExtract.ok, true);
assert.deepStrictEqual(normalizedExtract.headings, [
  { level: 1, heading: "【INFORMATION】／作品情報" },
]);
assert.ok(normalizedExtract.body.includes("- [黒崎一護](黒崎一護)"));
assert.ok(normalizedExtract.body.includes("![pixiv image 61456650](pixivimage:61456650)"));

const autoTitleExtract = pixiv.extractSource({
  sourceText: "*【INFORMATION】／作品情報\n本文",
});
assert.strictEqual(autoTitleExtract.ok, true);
assert.strictEqual(autoTitleExtract.title, "【INFORMATION】／作品情報");
assert.deepStrictEqual(autoTitleExtract.headings, [
  { level: 1, heading: "【INFORMATION】／作品情報" },
]);

// -- Extract: missing/empty input never fakes success ------------------
assert.strictEqual(pixiv.extractSource({ sourceText: "" }).ok, false);
assert.strictEqual(pixiv.extractSource({ sourceText: "" }).error, pixiv.MISSING_SOURCE);
assert.strictEqual(pixiv.extractSource({ sourceText: "<p></p>" }).ok, false);

// -- Extract: paste line-break normalization ---------------------------
// iOS/Safari clipboard paste can carry CRLF, a lone CR, or the Unicode
// line/paragraph separators (U+2028/U+2029) instead of LF. Built from code
// points, not literal source characters: U+2028/U+2029 are themselves
// ECMAScript line terminators, so embedding them literally in this test
// script's source would risk the exact bug being tested for.
const LINE_SEPARATOR = String.fromCharCode(0x2028);
const PARAGRAPH_SEPARATOR = String.fromCharCode(0x2029);
const NBSP = String.fromCharCode(0x00a0);
const FULLWIDTH_SPACE = String.fromCharCode(0x3000);

[
  { label: "CRLF", text: "<h1>Title</h1><p>Line one.\r\nLine two.</p>" },
  { label: "lone CR", text: "<h1>Title</h1><p>Line one.\rLine two.</p>" },
  { label: "U+2028 line separator", text: "<h1>Title</h1><p>Line one." + LINE_SEPARATOR + "Line two.</p>" },
  { label: "U+2029 paragraph separator", text: "<h1>Title</h1><p>Line one." + PARAGRAPH_SEPARATOR + "Line two.</p>" },
].forEach(function (c) {
  var result = pixiv.extractSource({ sourceText: c.text });
  assert.strictEqual(result.ok, true, c.label);
  assert.ok(result.body.indexOf("Line one.") !== -1, c.label);
  assert.ok(result.body.indexOf("Line two.") !== -1, c.label);
  assert.strictEqual(result.body.indexOf("\r"), -1, c.label);
  assert.strictEqual(result.body.indexOf(LINE_SEPARATOR), -1, c.label);
  assert.strictEqual(result.body.indexOf(PARAGRAPH_SEPARATOR), -1, c.label);
});

// -- Extract: NBSP / full-width space fold to a normal space ------------
const nbspExtract = pixiv.extractSource({
  sourceText: "<h1>Title</h1><p>Word" + NBSP + "gap." + FULLWIDTH_SPACE + "Another.</p>",
});
assert.strictEqual(nbspExtract.ok, true);
assert.strictEqual(nbspExtract.body.indexOf(NBSP), -1);
assert.strictEqual(nbspExtract.body.indexOf(FULLWIDTH_SPACE), -1);
assert.ok(nbspExtract.body.indexOf("Word gap. Another.") !== -1);

// -- Extract: trailing whitespace (including NBSP/full-width space) never
// survives into the extracted body --------------------------------------
const trailingWhitespaceExtract = pixiv.extractSource({
  sourceText: "<h1>Title</h1><p>Trailing space line.   " + NBSP + FULLWIDTH_SPACE + "  </p>",
});
assert.strictEqual(trailingWhitespaceExtract.ok, true);
const trailingLines = trailingWhitespaceExtract.body.split("\n");
assert.strictEqual(trailingLines[trailingLines.length - 1], "Trailing space line.");

// -- Extract: input-size budget, checked before decode/trim -------------
const atLimitText = "T".repeat(pixiv.MAX_SOURCE_LENGTH);
assert.strictEqual(pixiv.extractSource({ sourceText: atLimitText }).ok, true);

const overLimitText = "T".repeat(pixiv.MAX_SOURCE_LENGTH + 1);
const overLimitResult = pixiv.extractSource({ sourceText: overLimitText });
assert.strictEqual(overLimitResult.ok, false);
assert.strictEqual(overLimitResult.error, pixiv.SOURCE_TOO_LARGE);

// Padding that would vanish after trim() must still be rejected -- the
// budget check runs on the raw untrimmed/undecoded paste, not after it.
const overLimitByTrimmablePadding = "T".repeat(pixiv.MAX_SOURCE_LENGTH) + "   ";
const overLimitByPaddingResult = pixiv.extractSource({ sourceText: overLimitByTrimmablePadding });
assert.strictEqual(overLimitByPaddingResult.ok, false);
assert.strictEqual(overLimitByPaddingResult.error, pixiv.SOURCE_TOO_LARGE);

console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_pixiv_js_open_source_extract_contracts() -> None:
    result = subprocess.run(
        [
            "node",
            "-e",
            _PAGES_PIXIV_CONTRACT,
            str(PAGES_PIXIV_JS_SOURCE),
            str(URL_FIXTURE),
            str(NORMALIZATION_FIXTURE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed in this environment")
def test_pages_pixiv_js_is_valid_javascript() -> None:
    result = subprocess.run(
        ["node", "--check", str(PAGES_PIXIV_JS_SOURCE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_pages_pixiv_js_search_is_retired() -> None:
    """Regression: this is a retire-Search PR, not a fix-Search PR.

    The panel must no longer call ``fetch()`` against dic.pixiv.net, and the
    old Search-button contract (``loadArticle`` / a "Search" affordance /
    CORS fetch attempt) must not reappear.
    """
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert "fetch(" not in source
    assert "loadArticle" not in source
    assert '"cors"' not in source
    assert "mode:" not in source
    assert "Search" not in source


def test_pages_pixiv_js_never_claims_localhost_is_reachable() -> None:
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert "MCP execution is local-only" not in source
    assert "Local result URL" not in source
    assert "127.0.0.1:8765" not in source
    assert "dic.pixiv.net" in source


def test_pages_pixiv_js_never_embeds_line_terminator_chars_in_source() -> None:
    """U+2028/U+2029 are ECMAScript source line terminators.

    ``normalizeLineBreaks``/``normalizeSpaces`` must build those code points
    at runtime (``String.fromCharCode``), never write the literal characters
    into a regex/string literal in the shipped source -- a stray literal
    U+2028/U+2029 inside a regex literal is a syntax error, and even inside a
    string literal it is an easy-to-reintroduce footgun this file avoids.

    Compared via ``chr(0x2028)``/``chr(0x2029)`` rather than a literal
    character in this test's own source, so this file does not itself carry
    an invisible line/paragraph separator byte for a diff viewer to mangle.
    """
    source = PAGES_PIXIV_JS_SOURCE.read_text(encoding="utf-8")
    assert chr(0x2028) not in source
    assert chr(0x2029) not in source


def test_history_source_url_fixture_is_synthetic_only() -> None:
    """Non-blocking ask: URL fixtures stay synthetic, no article body text.

    The one real value used anywhere (``BLEACH`` / ``8852559``) is a title +
    revision id pulled from the manually-verified URL shape in
    docs/stub-pages.md, not dumped article content.
    """
    fixtures = json.loads(URL_FIXTURE.read_text(encoding="utf-8"))
    assert "_provenance" in fixtures
    dumped = json.dumps(fixtures, ensure_ascii=False)
    # No pixiv article prose -- only URLs, titles, and revision ids.
    assert "概要" not in dumped
    assert "来歴" not in dumped


# ═══ · niconico adapter ══════════════════════════════════════════════════════════════════════════════════

class NiconicoAdapterTest(unittest.TestCase):
    def test_search_url_exact_encoding(self):
        url = build_search_url(
            q="初音 ミク", targets="title,description", sort="+viewCounter",
            context="Ironmate", limit=100, offset=0,
            filters={"filters[viewCounter][gte]": 10},
        )
        self.assertEqual(
            url,
            "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
            "?q=%E5%88%9D%E9%9F%B3+%E3%83%9F%E3%82%AF"
            "&targets=title%2Cdescription"
            "&fields=contentId%2Ctitle%2Cdescription%2Ctags%2CcategoryTags%2CviewCounter%2CmylistCounter%2ClikeCounter%2ClengthSeconds%2CstartTime%2ClastCommentTime%2CcommentCounter%2Cgenre"
            "&_sort=%2BviewCounter&_offset=0&_limit=100&_context=Ironmate"
            "&filters%5BviewCounter%5D%5Bgte%5D=10",
        )

    def test_empty_q_is_retained_and_keyword_search_requires_targets(self):
        url = build_search_url(q="", sort="-startTime", context="Ironmate")
        self.assertIn("?q=&", url)
        with self.assertRaises(ValueError):
            build_search_url(q="keyword", sort="-startTime", context="Ironmate")

    def test_bounds_context_and_excluded_fields(self):
        for kwargs in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 100001}, {"context": "x" * 41}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_search_url(q="", sort="-startTime", context=kwargs.pop("context", "Ironmate"), **kwargs)
        with self.assertRaises(ValueError):
            build_search_url(q="", sort="-startTime", context="Ironmate", fields=("contentId", "userId"))
        for bad_filters in ({"fields": "contentId,userId,lastResBody"}, {"_context": "x" * 100}, {"_limit": 1000}, {"filtersX": 1}, {1: "x"}):
            with self.subTest(bad_filters=bad_filters), self.assertRaises(ValueError):
                build_search_url(q="", sort="-startTime", context="Ironmate", filters=bad_filters)

    def test_content_url_is_conservative(self):
        self.assertEqual(content_url("sm12345"), "https://nico.ms/sm12345")
        for value in ("", "sm/1", "初音"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                content_url(value)

    def test_fixture_normalization_drops_excluded_fields(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "niconico_snapshot_v2.json").read_text())
        source = build_search_url(q="", sort="-startTime", context="Ironmate")
        item = normalize_item(fixture["search"]["data"][0], source_url=source)
        self.assertEqual(item["identifier"], "sm12345")
        self.assertEqual(item["html_url"], "https://nico.ms/sm12345")
        self.assertIsNone(item["api_url"])
        self.assertNotIn("userId", item["data"])
        self.assertNotIn("lastResBody", item["data"])
        self.assertEqual(item["provider"], "niconico")
        self.assertEqual(item["kind"], "video")
        self.assertEqual(item["source_url"], source)
        self.assertIsNone(normalize_item({"contentId": ""}, source_url=source)["html_url"])
        self.assertIsNone(normalize_item({"contentId": "so-1"}, source_url=source)["html_url"])

    def test_snapshot_consistency_and_pagination(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "niconico_snapshot_v2.json").read_text())
        self.assertTrue(snapshot_consistent(fixture["version_before"], fixture["version_after"]))
        self.assertFalse(snapshot_consistent(fixture["version_before"], {"last_modified": "changed"}))
        self.assertTrue(classify_page(fixture["search"], offset=0, limit=10)["complete"])
        state = classify_page({"meta": {"totalCount": 100001}, "data": [{}]}, offset=99999, limit=1)
        self.assertEqual(state["next_offset"], 100000)
        self.assertFalse(state["complete"])
        final = classify_page({"meta": {"totalCount": 100001}, "data": [{}]}, offset=100000, limit=1)
        self.assertTrue(final["complete"])
        self.assertFalse(final["truncated"])
        beyond = classify_page({"meta": {"totalCount": 100002}, "data": [{}]}, offset=100000, limit=1)
        self.assertTrue(beyond["truncated"])
        self.assertIsNone(beyond["next_offset"])
        self.assertTrue(completion_state(fixture["version_before"], fixture["version_after"], final)["complete"])
        self.assertFalse(completion_state(fixture["version_before"], {"last_modified": "changed"}, final)["complete"])
        stalled = classify_page({"meta": {"totalCount": 5}, "data": []}, offset=0, limit=10)
        self.assertEqual(stalled["status"], "stalled")
        self.assertIsNone(stalled["next_offset"])
        invalid = classify_page({}, offset=0, limit=10)
        self.assertEqual(invalid["status"], "invalid")
        self.assertFalse(invalid["complete"])
        for page in (beyond, stalled, invalid, {"status": "ok", "complete": False, "truncated": False}):
            with self.subTest(page=page):
                self.assertFalse(completion_state(fixture["version_before"], fixture["version_after"], page)["complete"])

    def test_invalid_meta_is_not_complete(self):
        for meta in ({"status": 500, "totalCount": 1}, {"status": 200, "totalCount": None}, {"totalCount": "1"}, {}):
            with self.subTest(meta=meta):
                state = classify_page({"meta": meta, "data": [{}]}, offset=0, limit=1)
                self.assertEqual(state["status"], "invalid")
                self.assertFalse(state["complete"])

    def test_http_statuses_preserve_error_body_offline(self):
        headers = Message()
        for code, expected in ((400, "invalid_request"), (503, "maintenance")):
            error = HTTPError("https://example.test", code, "x", headers, io.BytesIO(b'{"meta":{"errorCode":"synthetic"}}'))
            with self.subTest(code=code), patch("mcp_toolcall_lab.adapters.niconico_adapter.time.monotonic", side_effect=[1.0, 1.25]):
                result = fetch_json("https://example.test", user_agent="Ironmate", opener=lambda *a, **k: (_ for _ in ()).throw(error))
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["error"]["meta"]["errorCode"], "synthetic")
            self.assertEqual(result["elapsed_seconds"], 0.25)

    def test_rate_delay_is_testable_without_sleep(self):
        self.assertEqual(next_delay_seconds(1.25), 1.25)
        self.assertEqual(next_delay_seconds(0.1, http_status=503), 300.0)


    def test_required_negative_cases_and_user_agent_header(self):
        with self.assertRaises(ValueError):
            build_search_url(q="", sort="", context="Ironmate")
        with self.assertRaises(ValueError):
            build_search_url(q="", sort="-startTime", context="")
        with self.assertRaises(ValueError):
            fetch_json("https://example.test", user_agent="", opener=lambda *a, **k: None)

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{}'

        seen = {}
        def opener(request, timeout):
            seen["ua"] = request.get_header("User-agent")
            return Response()
        with patch("mcp_toolcall_lab.adapters.niconico_adapter.time.monotonic", side_effect=[1.0, 1.1]):
            fetch_json("https://example.test", user_agent="Ironmate", opener=opener)
        self.assertEqual(seen["ua"], "Ironmate")
        self.assertFalse(snapshot_consistent({"last_modified": ""}, {"last_modified": ""}))

class VersionObservationTest(unittest.TestCase):
    # Independently authored timestamps and content; no live API requests.
    version = {"last_modified": "2026-10-01T05:00:00+09:00"}
    changed = {"last_modified": "2026-10-02T05:00:00+09:00"}

    def run_search(self, responses, **kwargs):
        seen, events, waits = [], [], []
        responses = iter(responses)

        def opener(request, timeout):
            seen.append(request)
            events.append(("request", request.full_url))
            self.assertEqual(request.get_header("User-agent"), "Ironmate-offline")
            self.assertEqual(timeout, 7)
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())

        def sleeper(delay):
            waits.append(delay)
            events.append(("wait", delay))

        # Each HTTP request takes exactly 1.25s in this deterministic clock.
        with patch("mcp_toolcall_lab.adapters.niconico_adapter.time.monotonic", side_effect=[
            t for i in range(20) for t in (i * 10.0, i * 10.0 + 1.25)
        ]):
            result = paged_search(q="", sort="-startTime", context="Ironmate-offline",
                                  user_agent="Ironmate-offline", timeout=7,
                                  opener=opener, sleeper=sleeper, **kwargs)
        self.assertEqual([event[0] for event in events],
                         ["request"] + [v for _ in waits for v in ("wait", "request")])
        return result, seen, waits

    @staticmethod
    def page(ids, total):
        return {"meta": {"status": 200, "totalCount": total},
                "data": [{"contentId": ident, "title": "synthetic", "userId": 123}
                         for ident in ids]}

    @staticmethod
    def http_error(code):
        return HTTPError(VERSION_URL, code, "synthetic", Message(),
                         io.BytesIO(b'{"meta":{"errorCode":"synthetic"}}'))

    def test_same_version_surrounds_all_pages_and_preserves_provenance(self):
        result, requests, waits = self.run_search([
            self.version, self.page(["sm9001"], 2), self.page(["sm9002"], 2), self.version,
        ], limit=1)
        self.assertTrue(result["complete"])
        self.assertTrue(result["snapshot_consistent"])
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(requests[0].full_url, VERSION_URL)
        self.assertEqual(requests[-1].full_url, VERSION_URL)
        self.assertEqual(waits, [1.25] * 3)
        self.assertEqual(result["retry_delay_seconds"], 1.25)
        self.assertEqual(result["version_before"]["last_modified"], self.version["last_modified"])
        self.assertEqual(result["version_after"]["last_modified"], self.version["last_modified"])
        for i, record in enumerate(result["records"]):
            self.assertEqual(record["source_url"], requests[i + 1].full_url)
            self.assertEqual(parse_qs(urlsplit(record["source_url"]).query)["_offset"], [str(i)])
            self.assertNotIn("userId", record["data"])

    def test_version_change_is_incomplete_even_when_pages_complete(self):
        result, _, _ = self.run_search([self.version, self.page(["sm9001"], 1), self.changed])
        self.assertTrue(result["page"]["complete"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["snapshot_consistent"])

    def test_http_version_failure_is_never_complete_before_or_after(self):
        for code, status in ((400, "invalid_request"), (503, "maintenance"), (500, "error")):
            for position in ("before", "after"):
                with self.subTest(code=code, position=position):
                    responses = [self.http_error(code)] if position == "before" else [
                        self.version, self.page([], 0), self.http_error(code),
                    ]
                    result, requests, _ = self.run_search(responses)
                    self.assertFalse(result["complete"])
                    self.assertFalse(result["snapshot_consistent"])
                    observation = result["version_" + position]
                    self.assertEqual(observation["status"], status)
                    self.assertEqual(observation["http_status"], code)
                    self.assertNotIn("last_modified", observation)
                    self.assertEqual(len(requests), 1 if position == "before" else 3)
                    self.assertEqual(result["retry_delay_seconds"], 300.0 if code == 503 else 1.25)

    def test_missing_last_modified_is_incomplete_before_or_after(self):
        for position in ("before", "after"):
            with self.subTest(position=position):
                responses = [{}] if position == "before" else [self.version, self.page([], 0), {}]
                result, _, _ = self.run_search(responses)
                self.assertFalse(result["complete"])
                self.assertEqual(result["version_" + position]["error_type"], "invalid_version")

    def test_malformed_json_and_network_failure_are_incomplete(self):
        for failure in (b'{', b'\xff', URLError("offline")):
            for position in ("before", "after"):
                with self.subTest(failure=failure, position=position):
                    responses = [failure] if position == "before" else [
                        self.version, self.page([], 0), failure,
                    ]
                    result, _, _ = self.run_search(responses)
                    self.assertFalse(result["complete"])
                    self.assertEqual(result["version_" + position]["status"], "error")

    def test_version_helper_rejects_invalid_shapes_types_and_dates(self):
        for payload in (None, [], "stamp", {"last_modified": None}, {"last_modified": 1},
                        {"last_modified": []}, {"last_modified": ""},
                        {"last_modified": "2026-02-30T05:00:00+09:00"},
                        {"last_modified": "2026-10-01"},
                        {"last_modified": "2026-10-01T05:00:00"}):
            with self.subTest(payload=payload):
                result = fetch_version(user_agent="offline", opener=lambda *a, **k:
                                       io.BytesIO(json.dumps(payload).encode()))
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error_type"], "invalid_version")
                self.assertNotIn("last_modified", result)

    def test_page_http_failure_keeps_classification_and_paces_final_version(self):
        for code, status in ((400, "invalid_request"), (503, "maintenance")):
            with self.subTest(code=code):
                result, requests, waits = self.run_search([
                    self.version, self.http_error(code), self.version,
                ])
                self.assertFalse(result["complete"])
                self.assertEqual(result["page"]["status"], status)
                self.assertEqual(result["page"]["fetch"]["http_status"], code)
                self.assertEqual(len(requests), 3)  # no search retry
                self.assertEqual(waits, [1.25, 300.0 if code == 503 else 1.25])

    def test_page_limit_and_invalid_pages_cannot_be_completed_by_matching_version(self):
        result, _, _ = self.run_search([
            self.version, self.page(["sm9001"], 2), self.version,
        ], limit=1, max_pages=1)
        self.assertFalse(result["complete"])
        self.assertTrue(result["page"]["truncated"])
        for payload in ([], {}, {"meta": {"totalCount": 1}, "data": [None]},
                        self.page([], 1)):
            with self.subTest(payload=payload):
                result, _, _ = self.run_search([self.version, payload, self.version])
                self.assertFalse(result["complete"])
                self.assertEqual(result["records"], [])

    def test_provider_offset_limit_is_still_truncated(self):
        result, _, _ = self.run_search([
            self.version, self.page(["sm9001"], 100002), self.version,
        ], offset=100000, limit=1)
        self.assertFalse(result["complete"])
        self.assertTrue(result["page"]["truncated"])


if __name__ == "__main__":
    unittest.main()
