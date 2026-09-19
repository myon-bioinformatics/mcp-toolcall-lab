"""wikipedia_tool.py -- no real network in default pytest.

This sandbox's own dev environment cannot reach en.wikipedia.org (agent
proxy denies the CONNECT, same story as scripts/fetch_tiny_cpu_gguf.py and
huggingface.co), so every test below mocks urllib.request.urlopen with a
realistic MediaWiki API response shape rather than hitting the network.
One real-network check exists at the bottom, gated behind
MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1 for a human or a CI runner with real
internet to opt into -- never on by default.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib.error import URLError

import pytest

from mcp_toolcall_lab.wikipedia_tool import (
    FIXTURE_ENV,
    WikipediaFetchError,
    _wiki_headings_to_atx,
    fetch_article_sections,
    fetch_wikipedia_article,
    fetch_wikipedia_section,
    load_wikipedia_article,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _mock_extract_response(monkeypatch: pytest.MonkeyPatch, *, title: str, extract: str, pageid: str = "1") -> None:
    payload = {
        "batchcomplete": "",
        "query": {"pages": {pageid: {"pageid": int(pageid), "ns": 0, "title": title, "extract": extract}}},
    }

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(payload)

    import mcp_toolcall_lab.wikipedia_tool as wt

    monkeypatch.setattr(wt.urllib.request, "urlopen", fake_urlopen)


def _mock_missing_page(monkeypatch: pytest.MonkeyPatch, *, title: str) -> None:
    payload = {"batchcomplete": "", "query": {"pages": {"-1": {"ns": 0, "title": title, "missing": ""}}}}

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(payload)

    import mcp_toolcall_lab.wikipedia_tool as wt

    monkeypatch.setattr(wt.urllib.request, "urlopen", fake_urlopen)


def test_wiki_headings_to_atx_maps_equals_to_hashes() -> None:
    text = "Intro para.\n\n== Geography ==\nBody one.\n\n=== Climate ===\nBody two.\n"
    atx = _wiki_headings_to_atx(text)
    assert "## Geography" in atx
    assert "### Climate" in atx
    assert "==" not in atx


def test_fetch_article_sections_parses_a_mocked_response(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = (
        "Yokohama is a mock municipality in this lab.\n\n"
        "== Geography ==\n"
        "Kanagawa Prefecture, south of Tokyo.\n\n"
        "== History ==\n"
        "The lab does not call a live encyclopedia.\n"
    )
    _mock_extract_response(monkeypatch, title="Yokohama", extract=extract)

    sections = fetch_article_sections("Yokohama")
    titles = [s.title for s in sections]
    assert titles == ["Yokohama", "Geography", "History"]
    assert "mock municipality" in sections[0].body
    assert "Kanagawa Prefecture" in sections[1].body
    assert "does not call a live encyclopedia" in sections[2].body


def test_fetch_article_sections_raises_on_missing_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_missing_page(monkeypatch, title="Some Nonexistent Page Xyz")
    with pytest.raises(WikipediaFetchError, match="no en.wikipedia.org article"):
        fetch_article_sections("Some Nonexistent Page Xyz")


def test_fetch_article_sections_raises_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def always_fails(request, timeout=0):  # noqa: ARG001
        raise URLError("no route")

    import mcp_toolcall_lab.wikipedia_tool as wt

    monkeypatch.setattr(wt.urllib.request, "urlopen", always_fails)
    with pytest.raises(WikipediaFetchError, match="could not fetch"):
        fetch_article_sections("Yokohama")


def test_fetch_wikipedia_section_lists_headings_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography ==\nBody.\n\n== History ==\nBody two.\n"
    _mock_extract_response(monkeypatch, title="Yokohama", extract=extract)

    rows = fetch_wikipedia_section("Yokohama")
    assert rows == [{"heading": "Yokohama"}, {"heading": "Geography"}, {"heading": "History"}]
    assert all("body" not in row for row in rows), "the pulldown listing must not include body text"


def test_fetch_wikipedia_section_returns_the_matched_body(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography ==\nKanagawa Prefecture, south of Tokyo.\n"
    _mock_extract_response(monkeypatch, title="Yokohama", extract=extract)

    rows = fetch_wikipedia_section("Yokohama", "Geography")
    assert rows == [
        {
            "heading": "Geography",
            "heading_markdown": "## Geography",
            "body": "Kanagawa Prefecture, south of Tokyo.",
        }
    ]


def test_fetch_wikipedia_section_heading_markdown_reflects_the_real_level(monkeypatch: pytest.MonkeyPatch) -> None:
    # "== Geography ==" (wiki level 2) -> ATX "##"; "=== Climate ===" (wiki
    # level 3, nested under it) -> ATX "###" -- heading_markdown must use
    # the section's own level, not a hardcoded "##".
    extract = "Intro.\n\n== Geography ==\nBody.\n\n=== Climate ===\nMild.\n"
    _mock_extract_response(monkeypatch, title="Yokohama", extract=extract)

    assert fetch_wikipedia_section("Yokohama", "Geography")[0]["heading_markdown"] == "## Geography"
    assert fetch_wikipedia_section("Yokohama", "Climate")[0]["heading_markdown"] == "### Climate"


def test_fetch_wikipedia_section_fuzzy_matches_a_partial_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography and climate ==\nBody.\n"
    _mock_extract_response(monkeypatch, title="Yokohama", extract=extract)

    rows = fetch_wikipedia_section("Yokohama", "geography")
    assert rows == [
        {"heading": "Geography and climate", "heading_markdown": "## Geography and climate", "body": "Body."}
    ]


def test_fetch_wikipedia_section_unmatched_heading_is_empty_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography ==\nBody.\n"
    _mock_extract_response(monkeypatch, title="Yokohama", extract=extract)

    assert fetch_wikipedia_section("Yokohama", "this heading does not exist xyz") == []


def test_shipped_fixture_is_used_instead_of_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "wikipedia" / "yokohama_extract.json"
    monkeypatch.setenv(FIXTURE_ENV, str(fixture))

    def fail_network(request, timeout=0):  # noqa: ARG001
        raise AssertionError("fixture path must not call urlopen")

    import mcp_toolcall_lab.wikipedia_tool as wt

    monkeypatch.setattr(wt.urllib.request, "urlopen", fail_network)
    article, lookup = load_wikipedia_article("Yokohama")
    assert lookup == "miss"
    assert article.canonical_title == "Yokohama"
    assert "14109" in article.extract
    assert "== Geography ==" in article.extract
    result = fetch_wikipedia_article("Yokohama, Japan")
    assert result["canonical_title"] == "Yokohama"
    assert any(row["heading"] == "Geography" for row in result["headings"])
    with pytest.raises(WikipediaFetchError, match="no en.wikipedia.org article"):
        load_wikipedia_article("This Title Is Not In The Fixture")


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA") != "1",
    reason="live network to en.wikipedia.org; opt in with MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1",
)
def test_live_wikipedia_fetch_finds_a_real_section() -> None:
    sections = fetch_article_sections("Yokohama")
    titles = {s.title.lower() for s in sections}
    assert titles, "a real Wikipedia article should have at least one section"
    assert any("geograph" in title or "history" in title for title in titles)
