from pathlib import Path
from urllib.error import HTTPError

import pytest

from mcp_toolcall_lab import pixiv_dictionary_tool as pt

ROOT = Path(__file__).resolve().parents[1]
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
