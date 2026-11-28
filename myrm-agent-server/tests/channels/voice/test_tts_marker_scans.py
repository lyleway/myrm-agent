"""The voice handler reads exactly the TTS markers its reference patterns read, in time proportional to the reply.

The reference patterns are the specification here, not the code under test. A directive body holds no ``]`` and a text
block ends at the first closing marker, and each pattern is tried again from every ``[[tts:`` that has not closed: a
reply of 100,000 characters made of nothing else takes seven to nine seconds.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from collections.abc import Callable, Iterator

import pytest

from app.channels.voice.handler import _tts_directives, _tts_text_blocks, parse_tts_directives, strip_tts_tags

_REFERENCE_DIRECTIVE_RE = re.compile(r"\[\[tts:([^\]]+)\]\]")
_REFERENCE_TEXT_RE = re.compile(r"\[\[tts:text\]\](.*?)\[\[/tts:text\]\]", re.DOTALL)


def _reference_directives(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(1)) for m in _REFERENCE_DIRECTIVE_RE.finditer(text)]


def _reference_text_blocks(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(1)) for m in _REFERENCE_TEXT_RE.finditer(text)]


def _reference_parse(text: str) -> tuple[dict[str, str], str | None]:
    directives: dict[str, str] = {}
    for match in _REFERENCE_DIRECTIVE_RE.finditer(text):
        raw = match.group(1).strip()
        if raw == "off":
            directives["off"] = "true"
            continue
        if raw == "text":
            continue
        for part in raw.split():
            if "=" in part:
                key, _, val = part.partition("=")
                directives[key.strip()] = val.strip()
    found = _REFERENCE_TEXT_RE.search(text)
    return directives, found.group(1).strip() if found else None


def _reference_strip(text: str) -> str:
    text = _REFERENCE_TEXT_RE.sub("", text)
    text = _REFERENCE_DIRECTIVE_RE.sub("", text)
    return text.strip()


_PIECES = (
    "[[tts:", "[[tts:text]]", "[[/tts:text]]", "]", "]]", "[", "off", " voice=nova ", "\n",
)  # fmt: skip


def _random_strings(count: int, longest: int, seed: int) -> Iterator[str]:
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(_PIECES) for _ in range(rng.randint(0, longest)))


class TestFindsWhatThePatternsFound:
    @pytest.mark.parametrize("length", range(6))
    def test_every_text_of_that_length(self, length: int) -> None:
        for pieces in itertools.product(_PIECES, repeat=length):
            text = "".join(pieces)
            assert list(_tts_directives(text)) == _reference_directives(text), text
            assert list(_tts_text_blocks(text)) == _reference_text_blocks(text), text

    def test_random_text(self) -> None:
        for text in _random_strings(40_000, 14, seed=73):
            assert list(_tts_directives(text)) == _reference_directives(text), repr(text)
            assert list(_tts_text_blocks(text)) == _reference_text_blocks(text), repr(text)

    @pytest.mark.parametrize(
        ("text", "directives"),
        [
            ("a [[tts:off]] b", [(2, 13, "off")]),
            ("[[tts:voice=nova provider=x]]", [(0, 29, "voice=nova provider=x")]),
            ("[[tts:a]][[tts:b]]", [(0, 9, "a"), (9, 18, "b")]),
            ("[[tts:]]", []),  # a body is not empty
            ("[[tts:a]", []),  # a directive closes with two brackets
            ("[[tts:a]x]]", []),  # at the first "]" or not at all
            ("[[tts:a\nb]]", [(0, 11, "a\nb")]),  # a body may span lines
            ("[[tts:[[tts:a]]", [(0, 15, "[[tts:a")]),  # the first opener takes the whole body
            ("[[tts:a [[tts:b]]", [(0, 17, "a [[tts:b")]),
        ],
    )
    def test_directive_examples(self, text: str, directives: list[tuple[int, int, str]]) -> None:
        assert list(_tts_directives(text)) == directives == _reference_directives(text)

    @pytest.mark.parametrize(
        ("text", "blocks"),
        [
            ("[[tts:text]]hi[[/tts:text]]", [(0, 27, "hi")]),
            ("a [[tts:text]]x\ny[[/tts:text]] b", [(2, 30, "x\ny")]),
            ("[[tts:text]][[/tts:text]]", [(0, 25, "")]),  # a block may be empty
            ("[[tts:text]]a[[/tts:text]][[tts:text]]b[[/tts:text]]", [(0, 26, "a"), (26, 52, "b")]),
            ("[[tts:text]]a[[tts:text]]b[[/tts:text]]", [(0, 39, "a[[tts:text]]b")]),  # ends at the first closer
            ("[[tts:text]]no closer", []),
            ("[[/tts:text]][[tts:text]]x", []),  # a closer before an opener closes nothing
        ],
    )
    def test_text_block_examples(self, text: str, blocks: list[tuple[int, int, str]]) -> None:
        assert list(_tts_text_blocks(text)) == blocks == _reference_text_blocks(text)


class TestPublicFunctions:
    @pytest.mark.parametrize("length", range(5))
    def test_every_text_of_that_length(self, length: int) -> None:
        for pieces in itertools.product(_PIECES, repeat=length):
            text = "".join(pieces)
            assert parse_tts_directives(text) == _reference_parse(text), text
            assert strip_tts_tags(text) == _reference_strip(text), text

    def test_random_text(self) -> None:
        for text in _random_strings(20_000, 14, seed=79):
            assert parse_tts_directives(text) == _reference_parse(text), repr(text)
            assert strip_tts_tags(text) == _reference_strip(text), repr(text)

    def test_the_first_block_is_the_speech(self) -> None:
        text = "x [[tts:text]] one [[/tts:text]] y [[tts:text]]two[[/tts:text]]"

        assert parse_tts_directives(text) == ({}, "one")

    def test_blocks_go_before_directives_so_a_block_opener_is_not_cut_as_a_directive(self) -> None:
        assert strip_tts_tags("a [[tts:text]]hidden[[/tts:text]] b [[tts:off]]") == "a  b"

    def test_a_block_opener_left_without_its_closer_is_cut_as_a_directive(self) -> None:
        assert strip_tts_tags("a [[tts:text]]b") == "a b"

    def test_a_directive_after_a_flood_of_failed_openers_is_found(self) -> None:
        text = "[[tts:a]x" * 2_000 + "[[tts:voice=nova]]"

        assert parse_tts_directives(text) == ({"voice": "nova"}, None)
        assert strip_tts_tags(text) == "[[tts:a]x" * 2_000


_HOSTILE: dict[str, Callable[[int], str]] = {
    "directive-openers": lambda n: "[[tts:" * (n // 6),
    "directive-openers-then-a-single-bracket": lambda n: "[[tts:a" * (n // 7) + "]x",
    "directive-openers-each-closed-by-one-bracket": lambda n: "[[tts:a]x" * (n // 9),
    "empty-directives": lambda n: "[[tts:]]" * (n // 8),
    "block-openers": lambda n: "[[tts:text]]" * (n // 12),
    "block-openers-then-a-cut-closer": lambda n: "[[tts:text]]a[[/tts:text]" * (n // 25),
    "directives": lambda n: "[[tts:voice=nova]] " * (n // 19),
    "blocks": lambda n: "[[tts:text]]a[[/tts:text]] " * (n // 27),
}


def _cost_of(text: str) -> float:
    """The least CPU seconds of two runs of both public functions over ``text``."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        parse_tts_directives(text)
        strip_tts_tags(text)
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The reply grows eightfold: linear time grows about eightfold, quadratic about sixty-four-fold.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_reply(self, family: str) -> None:
        # At this size a quadratic scan takes seven seconds or more and a linear one a few milliseconds, so a
        # regression fails here instead of running on into the long text.
        short = _cost_of(_HOSTILE[family](100_000))
        assert short < 0.5

        long = _cost_of(_HOSTILE[family](800_000))

        assert long < 24 * max(short, 0.001)
