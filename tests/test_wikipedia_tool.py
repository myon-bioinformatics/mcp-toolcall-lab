"""Wikipedia tool, extract cache, the stdlib /wiki form and the tool catalog: no real network."""
from __future__ import annotations

import io
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import URLError
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab.catalog import AVAILABLE_TOOLS, dispatch_tool, search_municipalities, search_stations, search_transaction_prices
from mcp_toolcall_lab.stub_front import (
    DEFAULT_CORPUS, StubState, WIKI_EXTRACT_NOTE, WIKI_PAGES_DISCLAIMER, load_corpus, make_handler, render_wiki_page, write_pages,
)
from mcp_toolcall_lab.wikipedia_tool import (
    FIXTURE_ENV, WikipediaArticle, WikipediaExtractCache, WikipediaFetchError, _wiki_headings_to_atx, fetch_article_sections,
    fetch_wikipedia_article, fetch_wikipedia_section, get_article_cache, load_wikipedia_article, reset_article_cache,
)


# ═══ · wikipedia tool ════════════════════════════════════════════════════════════════════════════════════
# wikipedia_tool.py -- no real network in default pytest.
#
# This sandbox's own dev environment cannot reach en.wikipedia.org (agent
# proxy denies the CONNECT, same story as scripts/fetch_tiny_cpu_gguf.py and
# huggingface.co), so every test below mocks urllib.request.urlopen with a
# realistic MediaWiki API response shape rather than hitting the network.
# One real-network check exists at the bottom, gated behind
# MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1 for a human or a CI runner with real
# internet to opt into -- never on by default.

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
        "Exampleville is a mock municipality in this lab.\n\n"
        "== Geography ==\n"
        "Example Region, a fictional area.\n\n"
        "== History ==\n"
        "The lab does not call a live encyclopedia.\n"
    )
    _mock_extract_response(monkeypatch, title="Exampleville", extract=extract)

    sections = fetch_article_sections("Exampleville")
    titles = [s.title for s in sections]
    assert titles == ["Exampleville", "Geography", "History"]
    assert "mock municipality" in sections[0].body
    assert "Example Region" in sections[1].body
    assert "does not call a live encyclopedia" in sections[2].body


def test_fetch_article_sections_bleach_shaped_empty_parent_is_inclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An H2 parent with only H3 children (BLEACH's "あらすじ") must keep those
    # children's headings/body in its own section body, stopping only at the
    # next H2 sibling -- same inclusive rule as the Pages #wiki JS (#33).
    extract = (
        "Lead.\n\n"
        "== あらすじ ==\n\n"
        "=== 死神代行篇 ===\n"
        "arc1 body\n\n"
        "=== 尸魂界篇 ===\n"
        "arc2 body\n\n"
        "== 登場人物 ==\n"
        "people body\n"
    )
    _mock_extract_response(monkeypatch, title="BLEACH", extract=extract)

    sections = fetch_article_sections("BLEACH")
    by_title = {section.title: section for section in sections}
    lead = by_title["あらすじ"]
    assert "### 死神代行篇" in lead.body
    assert "arc1 body" in lead.body
    assert "### 尸魂界篇" in lead.body
    assert "arc2 body" in lead.body
    assert "登場人物" not in lead.body


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
        fetch_article_sections("Exampleville")


def test_fetch_wikipedia_section_lists_headings_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography ==\nBody.\n\n== History ==\nBody two.\n"
    _mock_extract_response(monkeypatch, title="Exampleville", extract=extract)

    rows = fetch_wikipedia_section("Exampleville")
    assert rows == [{"heading": "Exampleville"}, {"heading": "Geography"}, {"heading": "History"}]
    assert all("body" not in row for row in rows), "the pulldown listing must not include body text"


def test_fetch_wikipedia_section_returns_the_matched_body(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography ==\nExample Region, a fictional area.\n"
    _mock_extract_response(monkeypatch, title="Exampleville", extract=extract)

    rows = fetch_wikipedia_section("Exampleville", "Geography")
    assert rows == [
        {
            "heading": "Geography",
            "heading_markdown": "## Geography",
            "body": "Example Region, a fictional area.",
        }
    ]


def test_fetch_wikipedia_section_heading_markdown_reflects_the_real_level(monkeypatch: pytest.MonkeyPatch) -> None:
    # "== Geography ==" (wiki level 2) -> ATX "##"; "=== Climate ===" (wiki
    # level 3, nested under it) -> ATX "###" -- heading_markdown must use
    # the section's own level, not a hardcoded "##".
    extract = "Intro.\n\n== Geography ==\nBody.\n\n=== Climate ===\nMild.\n"
    _mock_extract_response(monkeypatch, title="Exampleville", extract=extract)

    assert fetch_wikipedia_section("Exampleville", "Geography")[0]["heading_markdown"] == "## Geography"
    assert fetch_wikipedia_section("Exampleville", "Climate")[0]["heading_markdown"] == "### Climate"


def test_fetch_wikipedia_section_fuzzy_matches_a_partial_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography and climate ==\nBody.\n"
    _mock_extract_response(monkeypatch, title="Exampleville", extract=extract)

    rows = fetch_wikipedia_section("Exampleville", "geography")
    assert rows == [
        {"heading": "Geography and climate", "heading_markdown": "## Geography and climate", "body": "Body."}
    ]


def test_fetch_wikipedia_section_unmatched_heading_is_empty_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Intro.\n\n== Geography ==\nBody.\n"
    _mock_extract_response(monkeypatch, title="Exampleville", extract=extract)

    assert fetch_wikipedia_section("Exampleville", "this heading does not exist xyz") == []


def test_shipped_fixture_is_used_instead_of_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "wikipedia" / "exampleville_extract.json"
    monkeypatch.setenv(FIXTURE_ENV, str(fixture))

    def fail_network(request, timeout=0):  # noqa: ARG001
        raise AssertionError("fixture path must not call urlopen")

    import mcp_toolcall_lab.wikipedia_tool as wt

    monkeypatch.setattr(wt.urllib.request, "urlopen", fail_network)
    article, lookup = load_wikipedia_article("Exampleville")
    assert lookup == "miss"
    assert article.canonical_title == "Exampleville"
    assert "90001" in article.extract
    assert "== Geography ==" in article.extract
    result = fetch_wikipedia_article("Exampleville, Demo")
    assert result["canonical_title"] == "Exampleville"
    assert any(row["heading"] == "Geography" for row in result["headings"])
    with pytest.raises(WikipediaFetchError, match="no en.wikipedia.org article"):
        load_wikipedia_article("This Title Is Not In The Fixture")


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA") != "1",
    reason="live network to en.wikipedia.org; opt in with MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1",
)
def test_live_wikipedia_fetch_finds_a_real_section() -> None:
    sections = fetch_article_sections("Mathematics")
    titles = {s.title.lower() for s in sections}
    assert titles, "a real Wikipedia article should have at least one section"
    assert any("history" in title or "area" in title for title in titles)


# ═══ · wikipedia cache ═══════════════════════════════════════════════════════════════════════════════════
# TTL/LRU cache for Wikipedia extracts -- no real network in default pytest.

def _payload(*, title: str, extract: str, redirect_from: str | None = None, pageid: str = "1") -> dict:
    query: dict = {"pages": {pageid: {"pageid": int(pageid), "ns": 0, "title": title, "extract": extract}}}
    if redirect_from:
        query["redirects"] = [{"from": redirect_from, "to": title}]
    return {"batchcomplete": "", "query": query}


def _install_urlopen(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    import mcp_toolcall_lab.wikipedia_tool as wt

    monkeypatch.setattr(wt.urllib.request, "urlopen", handler)


def test_heading_switch_is_a_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead.\n\n== Geography ==\nSouth of Tokyo.\n\n== History ==\nA port.\n"
    calls = {"n": 0}

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        return _FakeResponse(_payload(title="Exampleville", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    listed = fetch_wikipedia_section("Exampleville")
    geography = fetch_wikipedia_section("Exampleville", "Geography")
    history = fetch_wikipedia_section("Exampleville", "History")

    assert calls["n"] == 1
    assert [row["heading"] for row in listed] == ["Exampleville", "Geography", "History"]
    assert geography[0]["body"] == "South of Tokyo."
    assert history[0]["body"] == "A port."
    cache = get_article_cache()
    assert cache.hits >= 2
    assert cache.misses == 1


def test_cache_miss_then_hit_same_title(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead.\n\n== Geography ==\nBody.\n"
    calls = {"n": 0}

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        return _FakeResponse(_payload(title="Exampleville", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    first, first_lookup = load_wikipedia_article("Exampleville")
    second, second_lookup = load_wikipedia_article("Exampleville")
    assert first_lookup == "miss"
    assert second_lookup == "hit"
    assert first is second
    assert calls["n"] == 1


def test_redirect_reuses_canonical_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead.\n\n== Geography ==\nBody.\n"
    calls = {"n": 0}

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        url = request.full_url
        alias = "Exampleville, Demo" if "Exampleville%2C+Demo" in url or "Exampleville,+Demo" in url else None
        return _FakeResponse(_payload(title="Exampleville", extract=extract, redirect_from=alias))

    _install_urlopen(monkeypatch, fake_urlopen)

    redirected, lookup = load_wikipedia_article("Exampleville, Demo")
    assert lookup == "miss"
    assert redirected.canonical_title == "Exampleville"
    canonical, canonical_lookup = load_wikipedia_article("Exampleville")
    assert canonical_lookup == "hit"
    assert canonical is redirected
    assert calls["n"] == 1

    # Asking for the canonical title first, then the redirect alias: the
    # second request still has to fetch to learn the mapping, but it must
    # land on the same entry rather than allocate a second slot.
    reset_article_cache()
    calls["n"] = 0
    first, _ = load_wikipedia_article("Exampleville")
    alias, alias_lookup = load_wikipedia_article("Exampleville, Demo")
    assert alias_lookup == "miss"
    assert alias is first
    assert len(get_article_cache()) == 1
    assert calls["n"] == 2


def test_ttl_expiry_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead.\n\n== Geography ==\nBody.\n"
    clock = {"t": 0.0}
    cache = WikipediaExtractCache(maxsize=4, ttl_seconds=10.0, monotonic=lambda: clock["t"])
    reset_article_cache(cache)

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(_payload(title="Exampleville", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    _, first_lookup = load_wikipedia_article("Exampleville")
    clock["t"] = 9.9
    _, still_hit = load_wikipedia_article("Exampleville")
    clock["t"] = 10.0
    _, expired = load_wikipedia_article("Exampleville")
    assert first_lookup == "miss"
    assert still_hit == "hit"
    assert expired == "miss"


def test_lru_evicts_oldest_canonical_article(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}
    cache = WikipediaExtractCache(maxsize=2, ttl_seconds=60.0, monotonic=lambda: clock["t"])
    reset_article_cache(cache)

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        url = str(getattr(request, "full_url", request))
        title = "Tokyo" if "Tokyo" in url else "Exampleville" if "Exampleville" in url else "Osaka"
        return _FakeResponse(_payload(title=title, extract=f"{title} lead.\n"))

    _install_urlopen(monkeypatch, fake_urlopen)

    load_wikipedia_article("Exampleville")
    clock["t"] = 1.0
    load_wikipedia_article("Tokyo")
    clock["t"] = 2.0
    load_wikipedia_article("Osaka")
    assert len(cache) == 2
    assert cache.get("Exampleville") is None
    assert cache.get("Tokyo") is not None
    assert cache.get("Osaka") is not None

    clock["t"] = 3.0
    load_wikipedia_article("Tokyo")  # hit, becomes most-recent
    clock["t"] = 4.0
    load_wikipedia_article("Exampleville")  # miss, should evict Osaka
    assert cache.get("Osaka") is None
    assert cache.get("Tokyo") is not None
    assert cache.get("Exampleville") is not None


def test_fetch_wikipedia_article_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead paragraph.\n\n== Geography ==\nSouth of Tokyo.\n"
    _install_urlopen(
        monkeypatch,
        lambda request, timeout=0: _FakeResponse(_payload(title="Exampleville", extract=extract)),
    )
    result = fetch_wikipedia_article("Exampleville")
    assert result["canonical_title"] == "Exampleville"
    assert result["lang"] == "en"
    assert result["extract"] == extract
    assert result["headings"][0] == {"heading": "Exampleville", "level": 1}
    assert {"heading": "Geography", "level": 2} in result["headings"]
    assert "body" not in result


def test_slow_miss_does_not_block_hit_on_a_warm_key() -> None:
    """A miss must not hold the cache lock while its loader runs.

    The stub uses ThreadingHTTPServer; a slow Wikipedia/fixture load of
    one title must not stall a concurrent hit on a different warm title.
    """

    cache = WikipediaExtractCache(maxsize=8, ttl_seconds=60.0)
    cache.put(WikipediaArticle(canonical_title="Exampleville", lang="en", extract="warm lead."))

    started_load = threading.Event()
    release_load = threading.Event()
    hit_finished = threading.Event()
    errors: list[BaseException] = []

    def slow_loader(title: str, lang: str) -> tuple[WikipediaArticle, tuple[str, ...]]:
        started_load.set()
        if not release_load.wait(timeout=5):
            raise TimeoutError("slow loader was never released")
        return WikipediaArticle(canonical_title=title, lang=lang, extract="slow lead."), ()

    def forbidden_loader(title: str, lang: str) -> tuple[WikipediaArticle, tuple[str, ...]]:
        raise AssertionError(f"loader should not run for warm key {title!r} ({lang})")

    def miss_worker() -> None:
        try:
            article, lookup = cache.get_or_load("Tokyo", "en", slow_loader)
            assert lookup == "miss"
            assert article.canonical_title == "Tokyo"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def hit_worker() -> None:
        try:
            if not started_load.wait(timeout=5):
                raise TimeoutError("slow miss never entered the loader")
            article, lookup = cache.get_or_load("Exampleville", "en", forbidden_loader)
            assert lookup == "hit"
            assert article.canonical_title == "Exampleville"
            assert article.extract == "warm lead."
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            hit_finished.set()

    miss_thread = threading.Thread(target=miss_worker)
    hit_thread = threading.Thread(target=hit_worker)
    miss_thread.start()
    hit_thread.start()
    assert hit_finished.wait(timeout=1.0), "hit on a warm key was blocked by an in-flight miss"
    release_load.set()
    miss_thread.join(timeout=5)
    hit_thread.join(timeout=5)
    assert errors == []
    assert cache.get("Tokyo") is not None
    assert cache.misses == 1
    assert cache.hits >= 1


def test_distinct_misses_overlap_instead_of_serializing_loaders() -> None:
    """Two different titles must be allowed to load at the same time."""

    cache = WikipediaExtractCache(maxsize=8, ttl_seconds=60.0)
    both_in_loader = threading.Barrier(3)
    release_load = threading.Event()
    errors: list[BaseException] = []

    def overlapping_loader(title: str, lang: str) -> tuple[WikipediaArticle, tuple[str, ...]]:
        both_in_loader.wait(timeout=5)
        if not release_load.wait(timeout=5):
            raise TimeoutError("overlapping loader was never released")
        return WikipediaArticle(canonical_title=title, lang=lang, extract=f"{title} lead."), ()

    def worker(title: str) -> None:
        try:
            article, lookup = cache.get_or_load(title, "en", overlapping_loader)
            assert lookup == "miss"
            assert article.canonical_title == title
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("Tokyo", "Osaka")]
    for thread in threads:
        thread.start()
    try:
        both_in_loader.wait(timeout=1.0)
    except threading.BrokenBarrierError as exc:
        release_load.set()
        for thread in threads:
            thread.join(timeout=5)
        raise AssertionError("distinct misses serialized; both loaders should have been running") from exc
    release_load.set()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert cache.misses == 2
    assert cache.get("Tokyo") is not None
    assert cache.get("Osaka") is not None


def test_cache_survives_concurrent_heading_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead.\n\n== Geography ==\nBody.\n"
    calls = {"n": 0}
    started = threading.Barrier(8)

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        return _FakeResponse(_payload(title="Exampleville", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            started.wait(timeout=5)
            load_wikipedia_article("Exampleville")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert calls["n"] == 1


def test_network_error_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    def always_fails(request, timeout=0):  # noqa: ARG001
        raise URLError("no route")

    _install_urlopen(monkeypatch, always_fails)
    with pytest.raises(WikipediaFetchError):
        load_wikipedia_article("Exampleville")
    cache = get_article_cache()
    assert len(cache) == 0
    assert cache.misses == 1
    assert cache.hits == 0


# ═══ · wikipedia ui ══════════════════════════════════════════════════════════════════════════════════════
# Stdlib stub /wiki form: escaped extract + server-rendered heading select.

FIXTURE = ROOT / "fixtures" / "wikipedia" / "exampleville_extract.json"


def test_wiki_form_is_empty_until_a_title_is_submitted() -> None:
    html = render_wiki_page()
    assert 'data-testid="wiki-title"' in html
    assert 'method="get" action="/wiki"' in html
    assert WIKI_PAGES_DISCLAIMER in html
    assert 'data-testid="wiki-extract"' not in html
    assert "markdown_to_html" not in html


def test_wiki_title_shows_escaped_plaintext_extract(monkeypatch) -> None:
    monkeypatch.setenv(FIXTURE_ENV, str(FIXTURE))
    html = render_wiki_page(title="Exampleville")
    assert 'data-testid="wiki-extract"' in html
    assert "code 90001" in html
    assert "== Geography ==" in html
    assert WIKI_EXTRACT_NOTE in html
    assert 'data-cache="miss"' in html
    assert 'data-testid="wiki-heading-select"' in html
    assert ">## Geography<" in html
    assert 'data-testid="wiki-canonical-title">Exampleville</strong>' in html
    # Second render of the same title is a cache hit and still does not refetch.
    html_hit = render_wiki_page(title="Exampleville")
    assert 'data-cache="hit"' in html_hit


def test_wiki_heading_options_show_atx_labels_but_bare_values(monkeypatch) -> None:
    # Docker GET /wiki option labels must match the Pages #wiki JS's "## Title"
    # display (#33 / issue #34 parity); the <option value> stays the bare
    # title so heading lookup / the querystring round-trip is unaffected.
    import io

    from mcp_toolcall_lab import wikipedia_tool as wt

    payload = {
        "batchcomplete": "",
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "ns": 0,
                    "title": "Exampleville",
                    "extract": "Lead.\n\n== Geography ==\nBody.\n\n=== Climate ===\nMild.\n",
                }
            }
        },
    }

    class _FakeResponse:
        def __init__(self) -> None:
            self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(wt.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse())
    html = render_wiki_page(title="Exampleville")
    assert '<option value="Geography">## Geography</option>' in html
    assert '<option value="Climate">### Climate</option>' in html


def test_wiki_heading_shows_section_body_without_html_injection(monkeypatch) -> None:
    monkeypatch.setenv(FIXTURE_ENV, str(FIXTURE))
    html = render_wiki_page(title="Exampleville", heading="Geography")
    assert 'data-testid="wiki-section"' in html
    assert "Example Region" in html
    assert 'value="Geography" selected' in html
    assert "<script>" not in html


def test_wiki_escapes_extract_that_looks_like_html(monkeypatch) -> None:
    import io

    from mcp_toolcall_lab import wikipedia_tool as wt

    payload = {
        "batchcomplete": "",
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "ns": 0,
                    "title": "Exampleville",
                    "extract": 'Lead <script>alert("xss")</script>\n\n== Geography ==\n<body onload=x>\n',
                }
            }
        },
    }

    class _FakeResponse:
        def __init__(self) -> None:
            self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(wt.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse())
    html = render_wiki_page(title="Exampleville", heading="Geography")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;body onload=x&gt;" in html
    assert 'data-testid="wiki-section"' in html


def test_pages_tree_hosts_browser_wikipedia_mcp_not_local_wiki_or_try_it(tmp_path: Path) -> None:
    index = write_pages(tmp_path / "site")
    html = index.read_text(encoding="utf-8")
    assert "Browser → Wikipedia directly by default" in html
    assert "stub_front serve" in html
    assert 'data-testid="pages-wiki-app"' in html
    assert 'data-mcp-url="' in html
    assert 'data-testid="pages-wiki-title"' in html
    assert 'data-testid="pages-wiki-form"' in html
    assert 'src="pages-wiki.js?v=' in html
    assert 'data-testid="wiki-title"' not in html
    assert 'action="/wiki"' not in html
    assert 'href="/wiki"' not in html
    assert 'href="#wiki"' in html
    assert "w/api.php" not in html
    assert "Browser → Wikipedia directly by default" in html
    assert "Try it (static, no MCP)" not in html
    assert "mcpToolcallLabStubDemo" not in html


def test_composer_page_links_to_wiki_without_implying_pages_backend() -> None:
    from mcp_toolcall_lab.stub_front import _page

    html = _page("chat_" + "c" * 24, [], sections=load_corpus(DEFAULT_CORPUS))
    assert 'href="/wiki"' in html
    assert "wikipedia.org" not in html
    assert "this server only" in html


def test_wiki_route_serves_the_fixture_article(monkeypatch) -> None:
    monkeypatch.setenv(FIXTURE_ENV, str(FIXTURE))
    state = StubState(sections=load_corpus(DEFAULT_CORPUS), mcp_url=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/wiki?title=Exampleville", timeout=5) as response:
            assert response.status == 200
            html = response.read().decode("utf-8")
        assert "code 90001" in html
        assert 'data-testid="wiki-heading-select"' in html
        with urlopen(
            f"http://{host}:{port}/wiki?title=Exampleville&heading=Geography", timeout=5
        ) as response:
            selected = response.read().decode("utf-8")
        assert "Example Region" in selected
        assert 'data-cache="hit"' in selected
    finally:
        server.shutdown()
        server.server_close()


def test_screenshot_script_invokes_playwright_cli() -> None:
    source = (ROOT / "scripts" / "wikipedia_article_screenshot.py").read_text(encoding="utf-8")
    assert '"playwright"' in source
    assert '"screenshot"' in source
    assert "--full-page" in source
    assert "wiki-title-entered.png" in source
    assert "wiki-heading-selected.png" in source
    workflow = (ROOT / ".github" / "workflows" / "wikipedia-article-screenshot.yml").read_text(
        encoding="utf-8"
    )
    assert "wikipedia_article_screenshot.py" in workflow
    assert "exampleville_extract.json" in workflow
    assert "workflow_dispatch" in workflow
    assert "pytest -q" in workflow
    assert "live_wikipedia" in workflow


# ═══ · catalog ═══════════════════════════════════════════════════════════════════════════════════════════

class MockCatalogTest(unittest.TestCase):
    def test_mock_catalog_has_multiple_tools(self):
        self.assertEqual(
            AVAILABLE_TOOLS,
            (
                "find_municipalities",
                "find_transaction_prices",
                "find_stations",
                "fetch_wikipedia_section",
                "fetch_wikipedia_article",
                "fetch_pixiv_dictionary_section",
                "fetch_pixiv_dictionary_article",
            ),
        )

    def test_municipality_search_is_deterministic(self):
        self.assertEqual(
            search_municipalities("exampleville"),
            [{"code": "90001", "name": "Exampleville", "prefecture": "Example Region"}],
        )

    def test_blank_municipality_query_is_empty_not_error(self):
        self.assertEqual(search_municipalities("  "), [])

    def test_japanese_municipality_query_is_empty_in_this_mock(self):
        self.assertEqual(search_municipalities("サンプル市"), [])

    def test_transaction_price_is_mock_data(self):
        result = search_transaction_prices("90001", 2025)
        self.assertEqual(result[0]["source"], "mock")
        self.assertEqual(result[0]["year"], 2025)

    def test_unknown_municipality_transaction_prices_are_empty(self):
        self.assertEqual(search_transaction_prices("00000", 2025), [])

    def test_station_search_for_unknown_municipality_is_empty(self):
        self.assertEqual(search_stations("00000"), [])

    def test_dispatch_tool_matches_search_helpers(self):
        self.assertEqual(
            dispatch_tool("find_municipalities", {"query": "Exampleville"}),
            search_municipalities("Exampleville"),
        )
        with self.assertRaises(KeyError):
            dispatch_tool("find_transaction_price", {"municipality_code": "90001", "year": 2025})

    def test_dispatch_tool_routes_to_fetch_wikipedia_section(self):
        # The only tool here that is not a deterministic mock -- mocked at
        # the dispatch boundary rather than hitting the network, since
        # this sandbox cannot reach en.wikipedia.org (see
        # tests/test_wikipedia_tool.py for the real fetch/parse coverage).
        with mock.patch("mcp_toolcall_lab.catalog.fetch_wikipedia_section") as fetch:
            fetch.return_value = [{"heading": "Geography", "body": "..."}]
            result = dispatch_tool("fetch_wikipedia_section", {"title": "Exampleville", "heading": "Geography"})
        fetch.assert_called_once_with("Exampleville", "Geography", lang="en")
        self.assertEqual(result, [{"heading": "Geography", "body": "..."}])

    def test_dispatch_tool_routes_to_fetch_wikipedia_article(self):
        with mock.patch("mcp_toolcall_lab.catalog.fetch_wikipedia_article") as fetch:
            fetch.return_value = {
                "canonical_title": "Exampleville",
                "lang": "en",
                "extract": "Lead.",
                "headings": [{"heading": "Exampleville", "level": 1}],
            }
            result = dispatch_tool("fetch_wikipedia_article", {"title": "Exampleville"})
        fetch.assert_called_once_with("Exampleville", lang="en")
        self.assertEqual(result["canonical_title"], "Exampleville")
        self.assertEqual(result["extract"], "Lead.")
