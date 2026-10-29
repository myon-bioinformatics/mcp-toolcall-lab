"""Rule-based compression for character-limited outputs (for example X posts).

The primary metric is the character count (``len(str)``: Unicode code points).
Token counts (o200k_base via the optional ``tiktoken`` package) and UTF-8 bytes
are recorded alongside it. The module is stdlib-only; tokens are reported as
``None`` when ``tiktoken`` is not installed.

Rules are ordered from "no information loss" to "readable but riskier".
Budget mode applies them one at a time until the text fits ``limit``. Text is
never truncated: when every rule has been tried and the text is still too long,
the result says so via ``fits=False`` and ``over_by``.

Basis (TalkCompress findings, o200k_base): ``≥``/``≤`` are one token and beat
"at least"/"at most"; ``>``/``<`` also win; ``≈`` costs more tokens than
"approximately", so it is used only in ``mode="chars"``. No emoji beats English
in tokens; dropping VS16 (U+FE0F) keeps the glyph and saves a character.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Callable, Sequence

MODES = ("tokens", "chars")


@dataclass(frozen=True)
class Metrics:
    chars: int
    utf8_bytes: int
    tokens: int | None


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    replacement: str
    # "lossless": same meaning, "symbol": standard symbol, "abbrev": readable
    # but may be ambiguous for an AI or a reader to restore.
    risk: str
    chars_only: bool

    def apply(self, text: str) -> str:
        return self.pattern.sub(lambda m: _match_case(m.group(0), self.replacement), text)


def _match_case(source: str, replacement: str) -> str:
    if source[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _r(name: str, pattern: str, replacement: str, risk: str, *, chars_only: bool = False,
       ignore_case: bool = True) -> Rule:
    flags = re.IGNORECASE if ignore_case else 0
    return Rule(name, re.compile(pattern, flags), replacement, risk, chars_only)


_NUM = r"(?=[-+]?[$€£¥]?\d)"

RULES: tuple[Rule, ...] = (
    _r("strip_vs16", "\ufe0f", "", "lossless"),
    _r("collapse_spaces", r"[ \t]{2,}", " ", "lossless"),
    _r("space_before_punct", r"[ \t]+(?=[,.;:!?])", "", "lossless"),
    _r("ellipsis", r"\.\.\.", "…", "lossless"),
    _r("at_least", r"\bat least " + _NUM, "≥", "symbol"),
    _r("at_most", r"\bat most " + _NUM, "≤", "symbol"),
    _r("greater_than", r"\b(?:greater|more) than " + _NUM, ">", "symbol"),
    _r("less_than", r"\b(?:less|fewer) than " + _NUM, "<", "symbol"),
    _r("percent", r"(?<=\d) ?percent\b", "%", "symbol"),
    _r("do_not", r"\bdo not\b", "don't", "lossless"),
    _r("does_not", r"\bdoes not\b", "doesn't", "lossless"),
    _r("is_not", r"\bis not\b", "isn't", "lossless"),
    _r("are_not", r"\bare not\b", "aren't", "lossless"),
    _r("cannot", r"\bcannot\b", "can't", "lossless"),
    _r("will_not", r"\bwill not\b", "won't", "lossless"),
    _r("i_am", r"\bI am\b", "I'm", "lossless", ignore_case=False),
    _r("it_is", r"\bit is(?= \w)", "it's", "lossless"),
    _r("and", r" and ", " & ", "abbrev"),
    _r("versus", r"\bversus\b", "vs", "abbrev"),
    _r("for_example", r"\bfor example\b", "e.g.", "abbrev"),
    _r("information", r"\binformation\b", "info", "abbrev"),
    _r("without", r"\bwithout\b", "w/o", "abbrev"),
    _r("with", r"\bwith\b", "w/", "abbrev"),
    _r("because", r"\bbecause\b", "b/c", "abbrev"),
    _r("approximately", r"\bapproximately ", "≈", "symbol", chars_only=True),
)

_TOKEN_COUNTER: list[Callable[[str], int] | None] = []


def _load_token_counter() -> Callable[[str], int] | None:
    if not _TOKEN_COUNTER:
        try:
            import tiktoken
        except ImportError:
            _TOKEN_COUNTER.append(None)
        else:
            encoding = tiktoken.get_encoding("o200k_base")
            _TOKEN_COUNTER.append(lambda s: len(encoding.encode(s)))
    return _TOKEN_COUNTER[0]


def measure(text: str, token_counter: Callable[[str], int] | None = None) -> Metrics:
    counter = token_counter or _load_token_counter()
    return Metrics(len(text), len(text.encode("utf-8")), counter(text) if counter else None)


@dataclass(frozen=True)
class CompressResult:
    text: str
    limit: int | None
    fits: bool
    over_by: int
    applied: tuple[str, ...]
    risky: tuple[str, ...]
    before: Metrics
    after: Metrics

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compress(text: str, limit: int | None = None, *, mode: str = "tokens",
             rules: Sequence[Rule] = RULES,
             token_counter: Callable[[str], int] | None = None) -> CompressResult:
    """Apply ``rules`` in order until ``text`` fits ``limit`` characters.

    ``limit=None`` applies every eligible rule. In ``mode="tokens"`` a rule is
    skipped when it would raise the token count (and ``chars_only`` rules are
    never used). The text is never truncated.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    counter = token_counter or _load_token_counter()
    before = measure(text, counter)
    current, applied = text, []
    for rule in rules:
        if limit is not None and len(current) <= limit:
            break
        if rule.chars_only and mode == "tokens":
            continue
        candidate = rule.apply(current)
        if candidate == current:
            continue
        if mode == "tokens" and counter and counter(candidate) > counter(current):
            continue
        current = candidate
        applied.append(rule.name)
    after = measure(current, counter)
    over_by = max(0, after.chars - limit) if limit is not None else 0
    by_name = {rule.name: rule for rule in rules}
    risky = tuple(name for name in applied if by_name[name].risk == "abbrev")
    return CompressResult(current, limit, over_by == 0, over_by, tuple(applied), risky,
                          before, after)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="?", help="text to compress (default: stdin)")
    parser.add_argument("--limit", type=int, default=None, help="character budget")
    parser.add_argument("--mode", choices=MODES, default="tokens")
    args = parser.parse_args(argv)
    source = sys.stdin.read() if args.text is None else args.text
    result = compress(source, args.limit, mode=args.mode)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.fits else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
