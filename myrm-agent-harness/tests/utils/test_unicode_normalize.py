"""unicode_normalize: Stream-Safe Text Format and a normalization that stays linear however many marks a text stacks."""

from __future__ import annotations

import random
import sys
import time
import unicodedata
from collections.abc import Callable
from functools import cache, partial

import pytest

from myrm_agent_harness.utils import unicode_normalize
from myrm_agent_harness.utils.unicode_normalize import NormalizationForm, normalize, stream_safe

_FORMS: tuple[NormalizationForm, ...] = ("NFC", "NFD", "NFKC", "NFKD")
_JOINER = "\u034f"
_LIMIT = 30

# Captured before any test patches the module, so the checks below never run through a spy.
_NFKD = partial(unicodedata.normalize, "NFKD")


def _longest_run(text: str) -> int:
    """The longest run of non-starters in the NFKD of ``text``: what UAX #15 section 13 bounds by 30."""
    longest = current = 0
    for char in _NFKD(text):
        current = current + 1 if unicodedata.combining(char) else 0
        longest = max(longest, current)
    return longest


def _mark_flags(char: str) -> list[bool]:
    return [unicodedata.combining(part) != 0 for part in _NFKD(char)]


def _trailing_marks(flags: list[bool]) -> int:
    return next((index for index, mark in enumerate(reversed(flags)) if not mark), len(flags))


def _by_the_process(text: str) -> str:
    """UAX #15 section 13 written out character by character from the NFKD of each character alone."""
    pieces: list[str] = []
    count = 0
    for char in text:
        flags = _mark_flags(char)
        leading = next((index for index, mark in enumerate(flags) if not mark), len(flags))
        if count + leading > _LIMIT:
            pieces.append(_JOINER)
            count = 0
        count = count + len(flags) if all(flags) else _trailing_marks(flags)
        pieces.append(char)
    return "".join(pieces)


@cache
def _holders() -> dict[str, list[bool]]:
    """Every character whose NFKD holds a mark, with the mark flags of that NFKD, found by decomposing every code point."""
    holders: dict[str, list[bool]] = {}
    for code in range(sys.maxunicode + 1):
        flags = _mark_flags(chr(code))
        if any(flags):
            holders[chr(code)] = flags
    return holders


# Starters (the regional indicator lies in the coarse range the scan covers without holding a mark), marks of several
# combining classes, characters that decompose into two marks, and precomposed letters that end in a mark.
_STARTERS = ("a", "e", "z", " ", "\u00e9", "\u0915", "\u304b", "\u1100", "\u4e2d", "\U0001f680", "\U0001f1fa")
_MARKS = ("\u0301", "\u0316", "\u0323", "\u0334", "\u0345", "\u3099", "\u093c", "\u0300", "\u0489")
_TWO_MARKS = ("\u0344", "\u0f73", "\u0f75", "\u0f81")
_MIXED = ("\u00e0", "\u0958", "\U0001d15e", "\u304c", "\u1e0b\u0323", "\u1f83")


def _random_text(rnd: random.Random) -> str:
    pieces: list[str] = []
    for _ in range(rnd.randint(1, 6)):
        pieces.append(rnd.choice(_STARTERS))
        kind = rnd.random()
        pool = _MARKS if kind < 0.45 else _TWO_MARKS if kind < 0.7 else (*_MIXED, *_MARKS, *_TWO_MARKS, _JOINER)
        pieces.extend(rnd.choice(pool) for _ in range(rnd.choice((0, 1, 5, 14, 15, 16, 29, 30, 31, 32, 45, 70))))
    return "".join(pieces)


class TestStreamSafe:
    def test_ascii_text_comes_back_as_it_is(self) -> None:
        text = "plain ascii, 123 <b>tags</b>"

        assert stream_safe(text) is text

    @pytest.mark.parametrize(
        "text",
        [
            "a" + "\u0301" * _LIMIT,
            "\u0344" * (_LIMIT // 2),
            "\u0f73" * (_LIMIT // 2),
            "a" + "\u0301\u0316" * (_LIMIT // 2),
            "e\u0301" * 5_000,
            "\u00e0" * 5_000,
            "ภาษาไทยเป็นภาษาที่มีวรรณยุกต์ ก็ ที่ นี้ เมื่อ",
            "اللُّغَةُ الْعَرَبِيَّةُ هِيَ لُغَةٌ جَمِيلَةٌ",
            "Tiếng Việt có nhiều dấu: ế, ộ, ặ, ữ",
            "हिन्दी में क़, ख़, ग़, ज़ जैसे अक्षर",
            "がぎぐげござじずぜぞだぢづでど" * 3,
            "한국어 텍스트 🚀✅ 🇺🇸",
        ],
        ids=[
            "thirty-marks",
            "fifteen-two-mark-characters",
            "fifteen-tibetan-vowels",
            "alternating-thirty",
            "composing-pairs",
            "precomposed",
            "thai",
            "arabic-with-diacritics",
            "vietnamese",
            "hindi",
            "japanese-voiced-kana",
            "korean-and-emoji",
        ],
    )
    def test_text_within_the_limit_comes_back_unchanged(self, text: str) -> None:
        assert _longest_run(text) <= _LIMIT
        assert stream_safe(text) == text

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a" + "\u0301" * 31, "a" + "\u0301" * 30 + _JOINER + "\u0301"),
            ("a" + "\u0301" * 61, "a" + "\u0301" * 30 + _JOINER + "\u0301" * 30 + _JOINER + "\u0301"),
            ("\u0344" * 16, "\u0344" * 15 + _JOINER + "\u0344"),
            ("a" + "\u0301" * 29 + "\u0344", "a" + "\u0301" * 29 + _JOINER + "\u0344"),
            ("a" + "\u0301" * 29 + "\u00e0", "a" + "\u0301" * 29 + "\u00e0"),
            ("a" + "\u0301" * 30 + _JOINER + "\u0301" * 30, "a" + "\u0301" * 30 + _JOINER + "\u0301" * 30),
            ("\U0001f1fa" * 12 + "\u0301" * 31, "\U0001f1fa" * 12 + "\u0301" * 30 + _JOINER + "\u0301"),
        ],
        ids=[
            "one-past-the-limit",
            "two-segments",
            "two-marks-in-one-character",
            "two-marks-would-pass-the-limit",
            "a-starter-with-a-mark-opens-a-new-run",
            "a-joiner-already-there",
            "a-run-of-starters-the-scan-also-covers",
        ],
    )
    def test_a_joiner_goes_before_the_character_that_passes_the_limit(self, text: str, expected: str) -> None:
        assert stream_safe(text) == expected

    def test_the_shortest_run_that_passes_the_limit_is_still_found(self) -> None:
        # The most marks a character can leave behind it, then the widest characters made of marks alone.
        holders = _holders()
        head = max(
            (char for char, flags in holders.items() if not all(flags)), key=lambda c: _trailing_marks(holders[c])
        )
        left = _trailing_marks(holders[head])
        carriers = -(-(_LIMIT + 1 - left) // 2)
        text = head + "\u0344" * carriers

        assert _longest_run(text) > _LIMIT >= _longest_run(head + "\u0344" * (carriers - 1))
        assert stream_safe(text) == head + "\u0344" * (carriers - 1) + _JOINER + "\u0344"
        assert unicode_normalize._table().long_runs.search(text)

    def test_every_character_that_holds_a_mark_is_known(self) -> None:
        assert set(unicode_normalize._table().profiles) == set(_holders())

    def test_matches_the_process_on_random_text(self) -> None:
        rnd = random.Random(2026)
        inserted = 0
        for _ in range(4_000):
            text = _random_text(rnd)
            result = stream_safe(text)

            assert result == _by_the_process(text), text.encode("unicode_escape")
            inserted += result != text
        assert inserted > 500

    def test_the_result_is_stream_safe_and_differs_by_joiners_alone(self) -> None:
        rnd = random.Random(7)
        for _ in range(2_000):
            text = _random_text(rnd)
            result = stream_safe(text)

            assert _longest_run(result) <= _LIMIT
            assert result.replace(_JOINER, "") == text.replace(_JOINER, "")
            if _longest_run(text) <= _LIMIT:
                assert result == text


class TestNormalize:
    def test_ascii_is_its_own_normal_form_in_every_form(self) -> None:
        text = "".join(map(chr, range(128)))

        for form in _FORMS:
            assert unicodedata.normalize(form, text) == text
            assert normalize(form, text) == text

    @pytest.mark.parametrize("form", _FORMS)
    def test_is_unicodedata_for_text_within_the_limit(self, form: NormalizationForm) -> None:
        rnd = random.Random(11)
        compared = 0
        for _ in range(3_000):
            text = _random_text(rnd)
            if _longest_run(text) > _LIMIT:
                continue
            assert normalize(form, text) == unicodedata.normalize(form, text), text.encode("unicode_escape")
            compared += 1
        assert compared > 1_000

    @pytest.mark.parametrize(
        "unit",
        ["\u0301\u0316", "\u0301", "\u0344", "\u0f73", "\u0316\u0301\u0334\u0323\u0345"],
        ids=["alternating", "one-class", "two-marks-each", "tibetan", "five-classes"],
    )
    @pytest.mark.parametrize("form", _FORMS)
    def test_unicodedata_never_gets_a_run_past_the_limit(
        self, monkeypatch: pytest.MonkeyPatch, form: NormalizationForm, unit: str
    ) -> None:
        # The first non-ASCII text builds the character table with thousands of calls of its own, and the spy would
        # count them when this is the first test of the process to run.
        normalize(form, unit)
        real = unicodedata.normalize
        longest: list[int] = []

        def spy(received_form: NormalizationForm, text: str) -> str:
            longest.append(_longest_run(text))
            return real(received_form, text)

        monkeypatch.setattr(unicodedata, "normalize", spy)

        normalize(form, "a" + unit * 3_000)

        assert len(longest) == 1
        assert longest[0] <= _LIMIT

    def test_hostile_marks_cost_a_fraction_of_the_plain_normalization(self) -> None:
        text = "a" + "\u0301\u0316" * 10_000

        def cpu(function: Callable[[], str]) -> float:
            best = float("inf")
            for _ in range(3):
                started = time.process_time()
                function()
                best = min(best, time.process_time() - started)
            return best

        plain = cpu(lambda: unicodedata.normalize("NFKC", text))
        guarded = cpu(lambda: normalize("NFKC", text))

        # Measured 109 ms against 2.8 ms; a guard that does nothing leaves the ratio at 1.
        assert guarded < plain / 10
