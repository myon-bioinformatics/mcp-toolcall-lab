"""Tests for char_compress (character-budget compression)."""
from __future__ import annotations

import importlib.util
import json
import re

import pytest

from mcp_toolcall_lab import char_compress as cc

SAMPLE = ("Status ℹ️: we need at least 3 reviewers and at most 10 comments... "
          "It is not done because the information is incomplete; "
          "approximately 40 percent with tests.")


def words(s: str) -> int:
    return len(s.split())


def by_name(name: str) -> cc.Rule:
    return next(r for r in cc.RULES if r.name == name)


@pytest.mark.parametrize(("name", "src", "out"), [
    ("strip_vs16", "ℹ️ ❤️", "ℹ ❤"),
    ("collapse_spaces", "a  b\t\tc d", "a b c d"),
    ("space_before_punct", "a , b . c ! d ?", "a, b. c! d?"),
    ("ellipsis", "wait...", "wait…"),
    ("at_least", "at least 3, At least $5, at least -2, at least he", "≥3, ≥$5, ≥-2, at least he"),
    ("at_most", "at most 10 and at most once", "≤10 and at most once"),
    ("greater_than", "more than 5, greater than €2, more than ever", ">5, >€2, more than ever"),
    ("less_than", "less than 5, fewer than 2, less than ideal", "<5, <2, less than ideal"),
    ("percent", "40 percent, 5percent, percentage", "40%, 5%, percentage"),
    ("do_not", "Do not go; do note", "Don't go; do note"),
    ("does_not", "it does not", "it doesn't"),
    ("is_not", "is not", "isn't"),
    ("are_not", "are not", "aren't"),
    ("cannot", "Cannot stop", "Can't stop"),
    ("will_not", "will not", "won't"),
    ("i_am", "I am here, i am, Iam", "I'm here, i am, Iam"),
    ("it_is", "It is done. Yes, it is.", "It's done. Yes, it is."),
    ("and", "cats and dogs, android", "cats & dogs, android"),
    ("versus", "A versus B", "A vs B"),
    ("for_example", "For example x", "E.g. x"),
    ("information", "information, informational", "info, informational"),
    ("without", "without", "w/o"),
    ("with", "with withdraw", "w/ withdraw"),
    ("because", "Because", "B/c"),
    ("approximately", "approximately 4", "≈4"),
])
def test_each_rule(name: str, src: str, out: str) -> None:
    assert by_name(name).apply(src) == out


def test_rule_order_and_risk_classes() -> None:
    names = [r.name for r in cc.RULES]
    assert names.index("without") < names.index("with")
    assert names[0] == "strip_vs16"
    assert {r.risk for r in cc.RULES} == {"lossless", "symbol", "abbrev"}
    assert [r.name for r in cc.RULES if r.chars_only] == ["approximately"]
    assert {r.name for r in cc.RULES if r.risk == "abbrev"} == {
        "and", "versus", "for_example", "information", "without", "with", "because"}


def test_match_case() -> None:
    assert cc._match_case("Do", "don't") == "Don't"
    assert cc._match_case("do", "don't") == "don't"
    assert cc._match_case("At", "≥") == "≥"
    assert cc._match_case("", "x") == "x"
    assert cc._match_case("X", "aB") == "AB"


def test_measure_counts_with_injected_counter() -> None:
    m = cc.measure("ℹ️é", words)
    assert m == cc.Metrics(chars=3, utf8_bytes=8, tokens=1)


def test_all_rules_word_counter_chars_mode() -> None:
    r = cc.compress(SAMPLE, mode="chars", token_counter=words)
    assert r.text == ("Status ℹ: we need ≥3 reviewers & ≤10 comments… It isn't done b/c "
                      "the info is incomplete; ≈40% w/ tests.")
    assert r.limit is None and r.fits and r.over_by == 0
    assert r.risky == ("and", "information", "with", "because")
    assert r.before == cc.Metrics(len(SAMPLE), len(SAMPLE.encode()), words(SAMPLE))
    assert r.after.chars == len(r.text)


def test_tokens_mode_skips_chars_only_rule() -> None:
    r = cc.compress("approximately 4", mode="tokens", token_counter=words)
    assert r.text == "approximately 4" and r.applied == ()


def test_tokens_mode_rejects_token_increase_but_keeps_equal() -> None:
    counter = lambda s: len(s) + 10 * s.count("&")  # noqa: E731
    r = cc.compress("a and b  c", mode="tokens", token_counter=counter)
    assert r.text == "a and b c" and r.applied == ("collapse_spaces",)
    tie = lambda s: 1  # noqa: E731
    assert cc.compress("a and b", token_counter=tie).text == "a & b"


def test_chars_mode_ignores_token_increase() -> None:
    counter = lambda s: 10 * s.count("&")  # noqa: E731
    assert cc.compress("a and b", mode="chars", token_counter=counter).text == "a & b"


def test_budget_stops_as_soon_as_it_fits() -> None:
    text = "ok ℹ️ and at least 3"
    r = cc.compress(text, limit=len(text) - 1, token_counter=words)
    assert r.applied == ("strip_vs16",) and r.fits and r.over_by == 0
    assert r.text == "ok ℹ and at least 3"
    exact = cc.compress(text, limit=len(text), token_counter=words)
    assert exact.applied == () and exact.text == text and exact.fits


def test_budget_never_truncates_and_reports_overflow() -> None:
    text = "x" * 50 + " and y"
    r = cc.compress(text, limit=10, token_counter=lambda s: 1)
    assert r.text == "x" * 50 + " & y"
    assert not r.fits and r.over_by == len(r.text) - 10 == 44
    assert r.limit == 10
    zero = cc.compress("", limit=0, token_counter=words)
    assert zero.fits and zero.over_by == 0


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError, match=re.escape("mode must be one of ('tokens', 'chars'), got 'bytes'")):
        cc.compress("x", mode="bytes")
    with pytest.raises(ValueError, match="^limit must be >= 0$"):
        cc.compress("x", limit=-1)
    cc.compress("x", limit=0, token_counter=words)


def test_custom_rules_sequence() -> None:
    rule = cc._r("x", "a", "b", "abbrev", ignore_case=False)
    r = cc.compress("aA", rules=[rule], token_counter=words)
    assert r.text == "bA" and r.risky == ("x",)
    assert cc._r("y", "a", "b", "lossless").pattern.flags & re.IGNORECASE
    assert not rule.chars_only
    assert rule.pattern.flags == re.compile("a").flags  # no IGNORECASE, nothing else
    with pytest.raises(TypeError):
        cc.Rule("z", re.compile("a"), "b", "abbrev")  # chars_only is explicit


@pytest.mark.parametrize("obj", [
    cc.Metrics(1, 1, None), cc.RULES[0],
    cc.compress("a", token_counter=words),
])
def test_dataclasses_are_frozen(obj: object) -> None:
    import dataclasses
    field = dataclasses.fields(obj)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field, None)


def test_to_dict_is_json_ready() -> None:
    d = cc.compress("a and b", token_counter=words).to_dict()
    assert d["applied"] == ("and",) and d["before"]["chars"] == 7
    json.dumps(d)


def test_no_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_TOKEN_COUNTER", [None])
    r = cc.compress("approximately 4 and x", mode="tokens")
    assert r.after.tokens is None and r.text == "approximately 4 & x"


def test_load_token_counter_without_tiktoken(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "tiktoken":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(cc, "_TOKEN_COUNTER", [])
    monkeypatch.setattr(builtins, "__import__", fake)
    assert cc._load_token_counter() is None
    assert cc._TOKEN_COUNTER == [None]


def test_cli(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_TOKEN_COUNTER", [words])
    assert cc.main(["a and b", "--limit", "5", "--mode", "chars"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["text"] == "a & b" and out["fits"] is True
    assert cc.main(["abcdef", "--limit", "1"]) == 1
    capsys.readouterr()
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("ℹ️"))
    assert cc.main([]) == 0
    raw = capsys.readouterr().out
    assert '"text": "ℹ"' in raw  # ensure_ascii=False keeps symbols readable


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cc.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "Rule-based compression for character-limited outputs (for example X posts)." in out
    assert re.search(r"character budget( |$)", out)
    assert re.search(r"text to compress \(default: stdin\)( |$)", out)
    assert "--limit LIMIT" in out and "--mode {tokens,chars}" in out


@pytest.mark.skipif(importlib.util.find_spec("tiktoken") is None, reason="tiktoken not installed")
class TestRealTokenizer:
    def test_real_counter_loads_and_symbols_win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cc, "_TOKEN_COUNTER", [])
        count = cc._load_token_counter()
        assert count is not None and cc._load_token_counter() is count
        assert count("≥3") < count("at least 3")
        assert count("≤3") < count("at most 3")
        assert count(" ≈4") >= count(" approximately 4")  # no token win in context

    def test_sample_real_metrics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cc, "_TOKEN_COUNTER", [])
        r = cc.compress(SAMPLE)
        assert r.after.chars < r.before.chars
        assert r.after.tokens <= r.before.tokens
        assert r.after.utf8_bytes < r.before.utf8_bytes
        assert "approximately" in r.text
