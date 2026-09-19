"""TTL/LRU cache for Wikipedia extracts -- no real network in default pytest."""

from __future__ import annotations

import io
import json
import threading
from urllib.error import URLError

import pytest

from mcp_toolcall_lab.wikipedia_tool import (
    WikipediaExtractCache,
    WikipediaFetchError,
    fetch_wikipedia_article,
    fetch_wikipedia_section,
    get_article_cache,
    load_wikipedia_article,
    reset_article_cache,
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
        return _FakeResponse(_payload(title="Yokohama", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    listed = fetch_wikipedia_section("Yokohama")
    geography = fetch_wikipedia_section("Yokohama", "Geography")
    history = fetch_wikipedia_section("Yokohama", "History")

    assert calls["n"] == 1
    assert [row["heading"] for row in listed] == ["Yokohama", "Geography", "History"]
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
        return _FakeResponse(_payload(title="Yokohama", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    first, first_lookup = load_wikipedia_article("Yokohama")
    second, second_lookup = load_wikipedia_article("Yokohama")
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
        alias = "Yokohama, Japan" if "Yokohama%2C+Japan" in url or "Yokohama,+Japan" in url else None
        return _FakeResponse(_payload(title="Yokohama", extract=extract, redirect_from=alias))

    _install_urlopen(monkeypatch, fake_urlopen)

    redirected, lookup = load_wikipedia_article("Yokohama, Japan")
    assert lookup == "miss"
    assert redirected.canonical_title == "Yokohama"
    canonical, canonical_lookup = load_wikipedia_article("Yokohama")
    assert canonical_lookup == "hit"
    assert canonical is redirected
    assert calls["n"] == 1

    # Asking for the canonical title first, then the redirect alias: the
    # second request still has to fetch to learn the mapping, but it must
    # land on the same entry rather than allocate a second slot.
    reset_article_cache()
    calls["n"] = 0
    first, _ = load_wikipedia_article("Yokohama")
    alias, alias_lookup = load_wikipedia_article("Yokohama, Japan")
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
        return _FakeResponse(_payload(title="Yokohama", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    _, first_lookup = load_wikipedia_article("Yokohama")
    clock["t"] = 9.9
    _, still_hit = load_wikipedia_article("Yokohama")
    clock["t"] = 10.0
    _, expired = load_wikipedia_article("Yokohama")
    assert first_lookup == "miss"
    assert still_hit == "hit"
    assert expired == "miss"


def test_lru_evicts_oldest_canonical_article(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}
    cache = WikipediaExtractCache(maxsize=2, ttl_seconds=60.0, monotonic=lambda: clock["t"])
    reset_article_cache(cache)

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        url = str(getattr(request, "full_url", request))
        title = "Tokyo" if "Tokyo" in url else "Yokohama" if "Yokohama" in url else "Osaka"
        return _FakeResponse(_payload(title=title, extract=f"{title} lead.\n"))

    _install_urlopen(monkeypatch, fake_urlopen)

    load_wikipedia_article("Yokohama")
    clock["t"] = 1.0
    load_wikipedia_article("Tokyo")
    clock["t"] = 2.0
    load_wikipedia_article("Osaka")
    assert len(cache) == 2
    assert cache.get("Yokohama") is None
    assert cache.get("Tokyo") is not None
    assert cache.get("Osaka") is not None

    clock["t"] = 3.0
    load_wikipedia_article("Tokyo")  # hit, becomes most-recent
    clock["t"] = 4.0
    load_wikipedia_article("Yokohama")  # miss, should evict Osaka
    assert cache.get("Osaka") is None
    assert cache.get("Tokyo") is not None
    assert cache.get("Yokohama") is not None


def test_fetch_wikipedia_article_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead paragraph.\n\n== Geography ==\nSouth of Tokyo.\n"
    _install_urlopen(
        monkeypatch,
        lambda request, timeout=0: _FakeResponse(_payload(title="Yokohama", extract=extract)),
    )
    result = fetch_wikipedia_article("Yokohama")
    assert result["canonical_title"] == "Yokohama"
    assert result["lang"] == "en"
    assert result["extract"] == extract
    assert result["headings"][0] == {"heading": "Yokohama", "level": 1}
    assert {"heading": "Geography", "level": 2} in result["headings"]
    assert "body" not in result


def test_cache_survives_concurrent_heading_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = "Lead.\n\n== Geography ==\nBody.\n"
    calls = {"n": 0}
    started = threading.Barrier(8)

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        return _FakeResponse(_payload(title="Yokohama", extract=extract))

    _install_urlopen(monkeypatch, fake_urlopen)

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            started.wait(timeout=5)
            load_wikipedia_article("Yokohama")
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
        load_wikipedia_article("Yokohama")
    cache = get_article_cache()
    assert len(cache) == 0
    assert cache.misses == 1
    assert cache.hits == 0
